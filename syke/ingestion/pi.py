from __future__ import annotations

import hashlib
import importlib
import logging
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar, cast

from syke.config_file import expand_path
from syke.ingestion.constants import ROLE_ASSISTANT, ROLE_USER
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn
from syke.ingestion.parsers import parse_timestamp, read_jsonl

AdapterT = TypeVar("AdapterT", bound=ObserveAdapter)


class _RegisterAdapter(Protocol):
    def __call__(self, source: str) -> Callable[[type[AdapterT]], type[AdapterT]]: ...


register_adapter = cast(
    _RegisterAdapter,
    importlib.import_module("syke.sense.registry").register_adapter,
)

logger = logging.getLogger(__name__)


@register_adapter("pi")
class PiAdapter(ObserveAdapter):
    source: str = "pi"

    _SESSIONS_DIR = "~/.pi/agent/sessions"

    def _sync_epoch(self) -> float:
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        if last_sync:
            return datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp()
        return 0.0

    def discover(self) -> list[Path]:
        root = expand_path(self._SESSIONS_DIR)
        if not root.exists():
            return []

        last_sync = self._sync_epoch()
        found: list[Path] = []
        for fpath in root.rglob("*.jsonl"):
            if not fpath.is_file():
                continue
            if fpath.stat().st_mtime < last_sync:
                continue
            found.append(fpath)

        found.sort(key=os.path.getmtime)
        return found

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        cutoff = since if since > 0 else self._sync_epoch()

        for fpath in self.discover():
            if cutoff and fpath.stat().st_mtime < cutoff:
                continue
            try:
                session = self._parse_session(fpath)
                if session is not None:
                    yield session
            except Exception as exc:
                logger.warning("Failed to parse Pi session %s: %s", fpath.name, exc)

    def _parse_session(self, fpath: Path) -> ObservedSession | None:
        lines = read_jsonl(fpath)
        if not lines:
            return None

        envelope = next((ln for ln in lines if ln.get("type") == "session"), None)
        session_id = None
        cwd = None
        start_time = None

        if envelope:
            sid = envelope.get("id")
            if isinstance(sid, str):
                session_id = sid
            cwd_val = envelope.get("cwd")
            if isinstance(cwd_val, str):
                cwd = cwd_val
            start_time = parse_timestamp(envelope)

        if not session_id:
            session_id = fpath.stem.rsplit("_", 1)[-1] if "_" in fpath.stem else fpath.stem
        if start_time is None:
            start_time = datetime.fromtimestamp(fpath.stat().st_mtime, tz=UTC)

        turns: list[ObservedTurn] = []
        metadata: dict[str, object] = {}

        for idx, line in enumerate(lines):
            line_type = line.get("type")

            if line_type == "message":
                msg = line.get("message")
                if not isinstance(msg, dict):
                    continue
                msg = cast(dict[str, object], msg)

                role_raw = msg.get("role")
                if not isinstance(role_raw, str):
                    continue
                if role_raw not in {ROLE_USER, ROLE_ASSISTANT, "user", "assistant"}:
                    continue
                role = ROLE_USER if role_raw == "user" else ROLE_ASSISTANT

                content = self._extract_content(msg.get("content"))
                timestamp = parse_timestamp(line) or parse_timestamp(msg) or start_time

                turns.append(
                    ObservedTurn(
                        role=role,
                        content=content,
                        timestamp=timestamp,
                        metadata={"source_line_index": idx, "source_event_type": "message"},
                    )
                )

            elif line_type == "model_change":
                provider = line.get("provider")
                model_id = line.get("modelId")
                if isinstance(provider, str):
                    metadata["provider"] = provider
                if isinstance(model_id, str):
                    metadata["model"] = model_id

            elif line_type == "thinking_level_change":
                level = line.get("thinkingLevel")
                if isinstance(level, str):
                    metadata["thinking_level"] = level

        root_path = fpath.parent
        relative_path = fpath.name
        source_instance_id = hashlib.sha256(
            f"{self.source}:{root_path}:{relative_path}".encode()
        ).hexdigest()[:12]

        if not turns:
            return ObservedSession(
                session_id=session_id,
                source_path=fpath,
                start_time=start_time,
                turns=[],
                metadata=metadata,
                source_instance_id=source_instance_id,
            )

        end_time = turns[-1].timestamp
        metadata["turn_count"] = len(turns)
        metadata["user_turns"] = sum(1 for t in turns if t.role == ROLE_USER)
        metadata["assistant_turns"] = sum(1 for t in turns if t.role == ROLE_ASSISTANT)
        metadata["duration_minutes"] = round(
            max(0.0, (end_time - start_time).total_seconds() / 60.0), 1
        )
        if cwd:
            metadata["cwd"] = cwd

        return ObservedSession(
            session_id=session_id,
            source_path=fpath,
            start_time=start_time,
            end_time=end_time,
            project=cwd,
            turns=turns,
            metadata=metadata,
            source_instance_id=source_instance_id,
        )

    @staticmethod
    def _extract_content(raw: object) -> str:
        if isinstance(raw, str):
            return raw
        if not isinstance(raw, list):
            return ""
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
