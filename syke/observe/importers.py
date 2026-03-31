"""Manual event ingestion gateway for CLI record surfaces."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.observe.content_filter import ContentFilter
from syke.models import Event

logger = logging.getLogger(__name__)


class IngestGateway:
    """Validates, filters, deduplicates, and inserts events from any source."""

    def __init__(self, db: SykeDB, user_id: str):
        self.db = db
        self.user_id = user_id
        self.filter = ContentFilter()

    def push(
        self,
        source: str,
        event_type: str,
        title: str,
        content: str,
        timestamp: str | None = None,
        metadata: dict[str, object] | None = None,
        external_id: str | None = None,
    ) -> dict[str, object]:
        """Push a single event. Returns {status, event_id, duplicate}."""
        if not source or not event_type or not content:
            return {"status": "error", "error": "source, event_type, and content are required"}

        filtered, reason = self.filter.process(content, title or "")
        if filtered is None:
            return {"status": "filtered", "reason": reason, "duplicate": False}

        if external_id and self.db.event_exists_by_external_id(source, self.user_id, external_id):
            return {"status": "duplicate", "external_id": external_id, "duplicate": True}

        try:
            ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now(UTC)
        except (ValueError, TypeError):
            return {"status": "error", "error": f"Invalid timestamp: {timestamp!r}"}
        if metadata is not None and not isinstance(metadata, dict):
            return {
                "status": "error",
                "error": f"metadata must be a dict, got {type(metadata).__name__}",
            }
        event = Event(
            id=str(uuid7()),
            user_id=self.user_id,
            source=source,
            timestamp=ts,
            event_type=event_type,
            title=title,
            content=filtered,
            metadata=metadata or {},
            external_id=external_id,
        )

        inserted = self.db.insert_event(event)
        if not inserted:
            return {"status": "duplicate", "event_id": event.id, "duplicate": True}

        logger.info("Push: %s/%s — %s", source, event_type, title)
        return {"status": "ok", "event_id": event.id, "duplicate": False}

    def push_batch(self, events: list[dict[str, object]]) -> dict[str, object]:
        """Push multiple events. Returns {status, inserted, duplicates, filtered}."""
        inserted = 0
        duplicates = 0
        filtered = 0
        errors = []

        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                errors.append(
                    {"index": i, "error": f"Event must be a dict, got {type(ev).__name__}"}
                )
                continue
            metadata = ev.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    errors.append({"index": i, "error": f"Invalid metadata JSON: {metadata!r}"})
                    continue
            if metadata is not None and not isinstance(metadata, dict):
                errors.append(
                    {"index": i, "error": f"metadata must be a dict, got {type(metadata).__name__}"}
                )
                continue

            source = ev.get("source", "")
            event_type = ev.get("event_type", "")
            title = ev.get("title", "")
            content = ev.get("content", "")
            timestamp = ev.get("timestamp")
            external_id = ev.get("external_id")

            result = self.push(
                source=source if isinstance(source, str) else "",
                event_type=event_type if isinstance(event_type, str) else "",
                title=title if isinstance(title, str) else "",
                content=content if isinstance(content, str) else "",
                timestamp=timestamp if isinstance(timestamp, str) else None,
                metadata=metadata,
                external_id=external_id if isinstance(external_id, str) else None,
            )
            if result["status"] == "ok":
                inserted += 1
            elif result["status"] == "duplicate":
                duplicates += 1
            elif result["status"] == "filtered":
                filtered += 1
            elif result["status"] == "error":
                errors.append({"index": i, "error": result["error"]})

        logger.info(
            "Push batch: %d inserted, %d duplicates, %d filtered (of %d)",
            inserted,
            duplicates,
            filtered,
            len(events),
        )
        return {
            "status": "ok" if not errors else "partial_error",
            "inserted": inserted,
            "duplicates": duplicates,
            "filtered": filtered,
            "errors": errors,
            "total": len(events),
        }


__all__ = ["IngestGateway"]
