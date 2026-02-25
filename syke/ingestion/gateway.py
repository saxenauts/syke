"""IngestGateway — validates, filters, and inserts events from external push sources (API)."""

from __future__ import annotations

import json
import logging
from datetime import datetime

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.ingestion.base import ContentFilter
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
        metadata: dict | None = None,
        external_id: str | None = None,
    ) -> dict:
        """Push a single event. Returns {status, event_id, duplicate}."""
        # 1. Validate required fields
        if not source or not event_type or not content:
            return {"status": "error", "error": "source, event_type, and content are required"}

        # 2. Content filter: skip or sanitize
        filtered, reason = self.filter.process(content, title or "")
        if filtered is None:
            return {"status": "filtered", "reason": reason, "duplicate": False}

        # 3. Check external_id dedup before building the full event
        if external_id and self.db.event_exists_by_external_id(source, self.user_id, external_id):
            return {"status": "duplicate", "external_id": external_id, "duplicate": True}

        # 4. Build Event
        try:
            ts = datetime.fromisoformat(timestamp) if timestamp else datetime.now()
        except (ValueError, TypeError):
            return {"status": "error", "error": f"Invalid timestamp: {timestamp!r}"}
        if metadata is not None and not isinstance(metadata, dict):
            return {"status": "error", "error": f"metadata must be a dict, got {type(metadata).__name__}"}
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

        # 5. Insert (natural-key dedup handled by DB UNIQUE constraint)
        inserted = self.db.insert_event(event)
        if not inserted:
            return {"status": "duplicate", "event_id": event.id, "duplicate": True}

        logger.info("Push: %s/%s — %s", source, event_type, title)
        return {"status": "ok", "event_id": event.id, "duplicate": False}

    def push_batch(self, events: list[dict]) -> dict:
        """Push multiple events. Returns {status, inserted, duplicates, filtered}."""
        inserted = 0
        duplicates = 0
        filtered = 0
        errors = []

        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                errors.append({"index": i, "error": f"Event must be a dict, got {type(ev).__name__}"})
                continue
            # Normalize metadata: parse strings, reject non-dicts
            metadata = ev.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    errors.append({"index": i, "error": f"Invalid metadata JSON: {metadata!r}"})
                    continue
            if metadata is not None and not isinstance(metadata, dict):
                errors.append({"index": i, "error": f"metadata must be a dict, got {type(metadata).__name__}"})
                continue

            result = self.push(
                source=ev.get("source", ""),
                event_type=ev.get("event_type", ""),
                title=ev.get("title", ""),
                content=ev.get("content", ""),
                timestamp=ev.get("timestamp"),
                metadata=metadata,
                external_id=ev.get("external_id"),
            )
            if result["status"] == "ok":
                inserted += 1
            elif result["status"] == "duplicate":
                duplicates += 1
            elif result["status"] == "filtered":
                filtered += 1
            elif result["status"] == "error":
                errors.append({"index": i, "error": result["error"]})

        logger.info("Push batch: %d inserted, %d duplicates, %d filtered (of %d)", inserted, duplicates, filtered, len(events))
        return {
            "status": "ok" if not errors else "partial_error",
            "inserted": inserted,
            "duplicates": duplicates,
            "filtered": filtered,
            "errors": errors,
            "total": len(events),
        }
