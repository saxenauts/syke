from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.models import Event

logger = logging.getLogger(__name__)

INGESTION_START = "ingestion.start"
INGESTION_COMPLETE = "ingestion.complete"
SYNTHESIS_START = "synthesis.start"
SYNTHESIS_COMPLETE = "synthesis.complete"
SYNTHESIS_SKIPPED = "synthesis.skipped"
DAEMON_CYCLE_START = "daemon.cycle.start"
DAEMON_CYCLE_COMPLETE = "daemon.cycle.complete"
SENSE_WATCHER_START = "sense.watcher.start"
SENSE_FILE_DETECTED = "sense.file.detected"
SENSE_BATCH_FLUSHED = "sense.batch.flushed"
HEALTH_CHECK = "health.check"
HEALING_TRIGGERED = "healing.triggered"
HEALING_COMPLETE = "healing.complete"
HEALING_FAILED = "healing.failed"
REGISTRY_ADAPTER_ADDED = "registry.adapter.added"
ASK_START = "ask.start"
ASK_COMPLETE = "ask.complete"
ASK_TOOL_USE = "ask.tool_use"
SYNTHESIS_TOOL_USE = "synthesis.tool_use"

SELF_OBSERVATION_EVENT_TYPES = (
    INGESTION_START,
    INGESTION_COMPLETE,
    SYNTHESIS_START,
    SYNTHESIS_COMPLETE,
    SYNTHESIS_SKIPPED,
    DAEMON_CYCLE_START,
    DAEMON_CYCLE_COMPLETE,
    SENSE_WATCHER_START,
    SENSE_FILE_DETECTED,
    SENSE_BATCH_FLUSHED,
    HEALTH_CHECK,
    HEALING_TRIGGERED,
    HEALING_COMPLETE,
    HEALING_FAILED,
    REGISTRY_ADAPTER_ADDED,
    ASK_START,
    ASK_COMPLETE,
    ASK_TOOL_USE,
    SYNTHESIS_TOOL_USE,
)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class SykeObserver:
    """Records source='syke' telemetry events.

    Thread-safe: creates a dedicated DB connection per thread on first use,
    reuses it for subsequent calls from the same thread.
    """

    def __init__(self, db: SykeDB, user_id: str):
        self.db_path = db.db_path
        self.user_id = user_id
        import threading
        self._local = threading.local()
        self._connections: list[SykeDB] = []
        self._connections_lock = threading.Lock()

    def close(self) -> None:
        with self._connections_lock:
            for db in self._connections:
                try:
                    db.close()
                except Exception:
                    pass
            self._connections.clear()

    def _get_db(self) -> SykeDB:
        db = getattr(self._local, "db", None)
        if db is None:
            db = SykeDB(self.db_path)
            self._local.db = db
            with self._connections_lock:
                self._connections.append(db)
        return db

    def record(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        run_id: str | None = None,
    ) -> None:
        payload = dict(data or {})
        duration_ms = payload.get("duration_ms")
        try:
            self._get_db().insert_event(
                Event(
                    id=str(uuid7()),
                    user_id=self.user_id,
                    source="syke",
                    timestamp=datetime.now(UTC),
                    event_type=event_type,
                    title=event_type,
                    content=json.dumps(payload, default=_json_default, sort_keys=True),
                    metadata={},
                    ingested_at=datetime.now(UTC),
                    external_id=f"syke:{event_type}:{uuid7()}",
                    duration_ms=int(duration_ms)
                    if isinstance(duration_ms, int | float)
                    else None,
                    extras={"observer_depth": 0, "run_id": run_id},
                )
            )
        except Exception:
            logger.warning("Failed to record self-observation event %s", event_type, exc_info=True)


__all__ = [
    "ASK_COMPLETE",
    "ASK_START",
    "ASK_TOOL_USE",
    "DAEMON_CYCLE_COMPLETE",
    "DAEMON_CYCLE_START",
    "HEALING_COMPLETE",
    "HEALING_FAILED",
    "HEALING_TRIGGERED",
    "HEALTH_CHECK",
    "INGESTION_COMPLETE",
    "INGESTION_START",
    "REGISTRY_ADAPTER_ADDED",
    "SELF_OBSERVATION_EVENT_TYPES",
    "SENSE_BATCH_FLUSHED",
    "SENSE_FILE_DETECTED",
    "SENSE_WATCHER_START",
    "SYNTHESIS_COMPLETE",
    "SYNTHESIS_SKIPPED",
    "SYNTHESIS_START",
    "SYNTHESIS_TOOL_USE",
    "SykeObserver",
]
