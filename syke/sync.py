"""Sync business logic — reusable by CLI and daemon."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from rich.console import Console
from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.models import IngestionResult


class _IngestAdapter(Protocol):
    def ingest(self, **kwargs: object) -> IngestionResult: ...


def sync_source(
    db: SykeDB,
    user_id: str,
    source: str,
    tracker,
    log: Console,
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
        log.print(f"  [dim]SKIP[/dim] {source} (no adapter)")
        return 0

    try:
        with tracker.track(f"sync_{source}") as metrics:
            result = adapter.ingest(**kwargs)
            metrics.events_processed = result.events_count

        if result.events_count > 0:
            log.print(f"  [green]+{result.events_count}[/green] {label}")
        else:
            log.print(f"  [dim] 0[/dim] {label}")
        return result.events_count
    except Exception as e:
        log.print(f"  [yellow]WARN[/yellow] {label}: {e}")
        return None


def _run_memory_synthesis(db: SykeDB, user_id: str, total_new: int, log: Console) -> None:
    try:
        from syke.llm.backends.pi_synthesis import pi_synthesize

        result = pi_synthesize(db, user_id)
        status = result.get("status", "unknown")
        if status == "completed":
            cost = result.get("cost_usd", 0)
            log.print(f"  [green]Memory synthesized.[/green] Cost: ${cost:.4f}")
        elif status == "skipped":
            log.print("  [dim]Memory synthesis skipped (below threshold)[/dim]")
        elif status == "failed":
            log.print(f"  [yellow]WARN[/yellow] Memory synthesis: {result.get('error', 'unknown')}")
    except Exception as e:
        log.print(f"  [yellow]WARN[/yellow] Memory synthesis failed: {e}")


def run_sync(
    db: SykeDB,
    user_id: str,
    out: Console | None = None,
) -> tuple[int, list[str]]:
    """Core sync logic reusable by CLI and daemon.

    Returns (total_new_events, list_of_synced_sources).

    Synthesis is skipped if fewer than SYNC_EVENT_THRESHOLD new events
    were found.
    """
    from syke.metrics import MetricsTracker

    tracker = MetricsTracker(user_id)
    log = out or Console()
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
        sources = db.get_sources(user_id)
        if not sources:
            return 0, []

        total_new = 0
        synced: list[str] = []

        for source in sources:
            count = sync_source(db, user_id, source, tracker, log)
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
                log.print(f"  [green]+{extra_pushed}[/green] pushed events (via CLI)")
                total_new += extra_pushed

        _run_memory_synthesis(db, user_id, total_new, log)
        from syke.distribution import refresh_distribution

        distribution = refresh_distribution(db, user_id)
        if distribution.memex_path:
            log.print(f"  [dim]Memex updated: {distribution.memex_path}[/dim]")
        if distribution.claude_include_ready:
            log.print("  [dim]Claude Code include ready[/dim]")
        if distribution.codex_memex_ready:
            log.print("  [dim]Codex memex reference ready[/dim]")
        if distribution.skill_paths:
            log.print(f"  [dim]Skills updated: {len(distribution.skill_paths)}[/dim]")
        for warning in distribution.warnings:
            log.print(f"  [yellow]WARN[/yellow] Distribution: {warning}")
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
