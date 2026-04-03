"""Sync business logic — reusable by CLI and daemon."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import cast

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.models import IngestionResult

logger = logging.getLogger(__name__)


class _IngestAdapter:
    def ingest(self, **kwargs: object) -> IngestionResult: ...


def sync_source(
    db: SykeDB,
    user_id: str,
    source: str,
    tracker,
    *,
    changed_paths: list[Path] | None = None,
) -> int | None:
    """Sync a single source. Returns count of new events."""
    kwargs: dict[str, object] = {}
    if changed_paths:
        kwargs["paths"] = changed_paths
    label = source

    from syke.config import user_data_dir
    from syke.observe.bootstrap import ensure_adapters
    from syke.observe.registry import HarnessRegistry, set_dynamic_adapters_dir

    adapters_dir = user_data_dir(user_id) / "adapters"
    set_dynamic_adapters_dir(adapters_dir)

    registry = HarnessRegistry(dynamic_adapters_dir=adapters_dir)
    adapter = cast(_IngestAdapter | None, registry.get_adapter(source, db, user_id))
    if adapter is None:
        ensure_adapters(user_id, sources=[source], registry=registry)
        adapter = cast(_IngestAdapter | None, registry.get_adapter(source, db, user_id))

    if adapter is None:
        logger.info("SKIP %s (no adapter)", source)
        return 0

    try:
        with tracker.track(f"sync_{source}") as metrics:
            result = adapter.ingest(**kwargs)
            metrics.events_processed = result.events_count

        if result.events_count > 0:
            logger.info("+%d %s", result.events_count, label)
        else:
            logger.info("0 %s", label)
        return result.events_count
    except Exception as e:
        logger.warning("%s: %s", label, e)
        return None


def _run_memory_synthesis(db: SykeDB, user_id: str, total_new: int) -> None:
    try:
        from syke.llm.backends.pi_synthesis import pi_synthesize

        result = pi_synthesize(db, user_id)
        status = result.get("status", "unknown")
        if status == "completed":
            cost = result.get("cost_usd", 0)
            logger.info("Memory synthesized. Cost: $%.4f", cost)
        elif status == "skipped":
            logger.info("Memory synthesis skipped (below threshold)")
        elif status == "failed":
            logger.warning("Memory synthesis: %s", result.get("error", "unknown"))
    except Exception as e:
        logger.warning("Memory synthesis failed: %s", e)


def run_sync(
    db: SykeDB,
    user_id: str,
    *,
    sources_override: list[str] | None = None,
) -> tuple[int, list[str]]:
    """Core sync logic reusable by CLI and daemon.

    Returns (total_new_events, list_of_synced_sources).

    Synthesis is skipped if fewer than SYNC_EVENT_THRESHOLD new events
    were found.
    """
    from syke.metrics import MetricsTracker

    tracker = MetricsTracker(user_id)
    observer_api = import_module("syke.observe.trace")
    observer = observer_api.SykeObserver(db, user_id)
    run_id = str(uuid7())
    started_at = datetime.now(UTC)
    observer.record(
        observer_api.INGESTION_START,
        {"start_time": started_at.isoformat()},
        run_id=run_id,
    )

    try:
        sources = list(dict.fromkeys(sources_override or db.get_sources(user_id)))
        if not sources:
            return 0, []

        total_new = 0
        synced: list[str] = []

        for source in sources:
            count = sync_source(db, user_id, source, tracker)
            if count is None:
                continue
            total_new += count
            if source != "chatgpt":
                synced.append(source)

        # Also count events pushed via CLI (federated push path) since last synthesis.
        last_synthesis_ts = db.get_last_synthesis_timestamp(user_id)
        if last_synthesis_ts:
            pushed_since = db.count_events_since(user_id, last_synthesis_ts)
            extra_pushed = max(0, pushed_since - total_new)
            if extra_pushed > 0:
                logger.info("+%d pushed events (via CLI)", extra_pushed)
                total_new += extra_pushed

        _run_memory_synthesis(db, user_id, total_new)
        from syke.distribution import refresh_distribution

        distribution = refresh_distribution(db, user_id)
        if distribution.memex_path:
            logger.info("Memex updated: %s", distribution.memex_path)
        if distribution.claude_include_ready:
            logger.info("Claude Code include ready")
        if distribution.codex_memex_ready:
            logger.info("Codex memex reference ready")
        if distribution.skill_paths:
            logger.info("Skills updated: %d", len(distribution.skill_paths))
        for warning in distribution.warnings:
            logger.warning("Distribution: %s", warning)
        return total_new, synced
    finally:
        ended_at = datetime.now(UTC)
        observer.record(
            observer_api.INGESTION_COMPLETE,
            {
                "start_time": started_at.isoformat(),
                "end_time": ended_at.isoformat(),
                "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                "events_count": locals().get("total_new", 0),
                "sources": locals().get("synced", []),
            },
            run_id=run_id,
        )
