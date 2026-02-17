"""ChatGPT ZIP export adapter."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from syke.ingestion.base import BaseAdapter
from syke.models import Event, IngestionResult


class ChatGPTAdapter(BaseAdapter):
    source = "chatgpt"

    def ingest(self, **kwargs) -> IngestionResult:
        """Ingest a ChatGPT export ZIP file."""
        file_path = kwargs.get("file_path")
        if not file_path:
            raise ValueError("file_path is required for ChatGPT ingestion")

        run_id = self.db.start_ingestion_run(self.user_id, self.source)

        try:
            events = self._parse_export(Path(file_path))
            count = self.db.insert_events(events)
            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                run_id=run_id, source=self.source, user_id=self.user_id,
                events_count=count,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, 0, error=str(e))
            raise

    def _parse_export(self, file_path: Path) -> list[Event]:
        """Parse conversations.json from the ChatGPT export ZIP."""
        events = []

        with zipfile.ZipFile(file_path, "r") as zf:
            # Find conversations.json
            conv_files = [n for n in zf.namelist() if n.endswith("conversations.json")]
            if not conv_files:
                raise ValueError("No conversations.json found in ZIP")

            data = json.loads(zf.read(conv_files[0]))

        for conv in data:
            title = conv.get("title", "Untitled")
            create_time = conv.get("create_time")
            update_time = conv.get("update_time")

            # Parse timestamp
            if create_time:
                timestamp = datetime.fromtimestamp(create_time, UTC)
            elif update_time:
                timestamp = datetime.fromtimestamp(update_time, UTC)
            else:
                continue

            # Build conversation content from the message mapping
            mapping = conv.get("mapping", {})
            messages = []
            for node_id, node in mapping.items():
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

            # Run content filter — skip private messaging, sanitize credentials
            filtered, reason = self.content_filter.process(content, title)
            if filtered is None:
                continue
            content = filtered

            # Truncate very long conversations to keep DB manageable
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
