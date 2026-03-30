"""Legacy one-off ingestion paths plus gateway push helpers."""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.observe.content_filter import ContentFilter
from syke.models import Event, IngestionResult

logger = logging.getLogger(__name__)


class ChatGPTAdapter:
    source = "chatgpt"

    def __init__(self, db, user_id: str):
        self.db = db
        self.user_id = user_id
        self.content_filter = ContentFilter()

    def ingest(self, **kwargs) -> IngestionResult:
        """Ingest a legacy ChatGPT export ZIP file."""
        file_path = kwargs.get("file_path")
        if not file_path:
            raise ValueError("file_path is required for ChatGPT ingestion")

        run_id = self.db.start_ingestion_run(self.user_id, self.source)

        try:
            events = self._parse_export(Path(file_path))
            count = self.db.insert_events(events)
            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                run_id=run_id,
                source=self.source,
                user_id=self.user_id,
                events_count=count,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, 0, error=str(e))
            raise

    def _parse_export(self, file_path: Path) -> list[Event]:
        """Parse conversations.json from a legacy ChatGPT export ZIP."""
        events = []

        with zipfile.ZipFile(file_path, "r") as zf:
            conv_files = [n for n in zf.namelist() if n.endswith("conversations.json")]
            if not conv_files:
                raise ValueError("No conversations.json found in ZIP")

            data = json.loads(zf.read(conv_files[0]))

        for conv in data:
            title = conv.get("title", "Untitled")
            create_time = conv.get("create_time")
            update_time = conv.get("update_time")

            if create_time:
                timestamp = datetime.fromtimestamp(create_time, UTC)
            elif update_time:
                timestamp = datetime.fromtimestamp(update_time, UTC)
            else:
                continue

            mapping = conv.get("mapping", {})
            messages = []
            for _node_id, node in mapping.items():
                msg = node.get("message")
                if not msg:
                    continue
                role = msg.get("author", {}).get("role", "unknown")
                parts = msg.get("content", {}).get("parts", [])
                text_parts = [p for p in parts if isinstance(p, str) and p.strip()]
                if text_parts:
                    text = "\n".join(text_parts)
                    messages.append(f"[{role}]: {text}")

            if not messages:
                continue

            content = "\n\n".join(messages)

            filtered, reason = self.content_filter.process(content, title)
            if filtered is None:
                continue
            content = filtered

            if len(content) > 50000:
                content = content[:50000] + "\n\n[...truncated]"

            events.append(
                Event(
                    user_id=self.user_id,
                    source=self.source,
                    timestamp=timestamp,
                    event_type="conversation",
                    title=title,
                    content=content,
                    metadata={
                        "conversation_id": conv.get("id", ""),
                        "message_count": len(messages),
                        "model": conv.get("default_model_slug", ""),
                        "update_time": update_time,
                    },
                )
            )

        return events


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


__all__ = ["ChatGPTAdapter", "IngestGateway"]
