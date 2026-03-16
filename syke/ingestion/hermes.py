from __future__ import annotations

import importlib
import json
import logging
import sqlite3
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar, cast

from syke.config_file import expand_path
from syke.ingestion.constants import ROLE_ASSISTANT, ROLE_USER
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn

AdapterT = TypeVar("AdapterT", bound=ObserveAdapter)


class _RegisterAdapter(Protocol):
    def __call__(self, source: str) -> Callable[[type[AdapterT]], type[AdapterT]]: ...


register_adapter = cast(
    _RegisterAdapter,
    importlib.import_module("syke.sense.registry").register_adapter,
)

logger = logging.getLogger(__name__)


@register_adapter("hermes")
class HermesAdapter(ObserveAdapter):
    source: str = "hermes"

    _DB_PATH = "~/.hermes/state.db"

    def discover(self) -> list[Path]:
        db_path = expand_path(self._DB_PATH)
        if db_path.exists():
            return [db_path]
        return []

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        paths = self.discover()
        if not paths:
            return

        db_path = paths[0]
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            logger.warning("Cannot open Hermes DB at %s: %s", db_path, exc)
            return

        try:
            yield from self._read_sessions(conn, db_path)
        finally:
            conn.close()

    def _read_sessions(
        self,
        conn: sqlite3.Connection,
        db_path: Path,
    ) -> Iterable[ObservedSession]:
        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()

        for sess in rows:
            try:
                session = self._parse_session(conn, sess, db_path)
                if session is not None:
                    yield session
            except Exception as exc:
                logger.warning(
                    "Failed to parse Hermes session %s: %s",
                    sess["id"],
                    exc,
                )

    def _parse_session(
        self,
        conn: sqlite3.Connection,
        sess: sqlite3.Row,
        db_path: Path,
    ) -> ObservedSession | None:
        session_id = sess["id"]

        started_at = sess["started_at"]
        start_time = (
            datetime.fromtimestamp(started_at, tz=UTC)
            if isinstance(started_at, (int, float))
            else datetime.now(UTC)
        )

        ended_at = sess["ended_at"]
        end_time = (
            datetime.fromtimestamp(ended_at, tz=UTC) if isinstance(ended_at, (int, float)) else None
        )

        msg_rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()

        turns: list[ObservedTurn] = []
        seen_content: set[tuple[str, str]] = set()

        for idx, msg in enumerate(msg_rows):
            role_raw = msg["role"]
            if role_raw not in {"user", "assistant", "system", "tool"}:
                continue

            if role_raw == "system":
                continue

            content = msg["content"] or ""
            timestamp_val = msg["timestamp"]
            ts = (
                datetime.fromtimestamp(timestamp_val, tz=UTC)
                if isinstance(timestamp_val, (int, float))
                else start_time
            )

            dedup_key = (role_raw, content[:200])
            if dedup_key in seen_content:
                continue
            seen_content.add(dedup_key)

            role = ROLE_USER if role_raw == "user" else ROLE_ASSISTANT

            tool_calls: list[dict[str, object]] = []

            tool_calls_json = msg["tool_calls"]
            if tool_calls_json and isinstance(tool_calls_json, str):
                try:
                    parsed = json.loads(tool_calls_json)
                    if isinstance(parsed, list):
                        for tc in parsed:
                            if isinstance(tc, dict):
                                tool_calls.append(
                                    {
                                        "block_type": "tool_use",
                                        "tool_name": tc.get("function", {}).get("name", ""),
                                        "tool_id": tc.get("id", ""),
                                        "input": tc.get("function", {}).get("arguments", ""),
                                    }
                                )
                except json.JSONDecodeError:
                    pass

            if role_raw == "tool":
                tool_name = msg["tool_name"] or ""
                tool_call_id = msg["tool_call_id"] or ""
                tool_calls.append(
                    {
                        "block_type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "tool_name": tool_name,
                        "content": content,
                        "is_error": False,
                    }
                )
                role = ROLE_ASSISTANT

            turns.append(
                ObservedTurn(
                    role=role,
                    content=content,
                    timestamp=ts,
                    tool_calls=tool_calls,
                    metadata={
                        "source_line_index": idx,
                        "source_event_type": role_raw,
                    },
                )
            )

        metadata: dict[str, object] = {}
        model = sess["model"]
        if isinstance(model, str) and model:
            metadata["model"] = model
        title = sess["title"]
        if isinstance(title, str) and title:
            metadata["title"] = title
        input_tokens = sess["input_tokens"]
        if isinstance(input_tokens, int):
            metadata["input_tokens"] = input_tokens
        output_tokens = sess["output_tokens"]
        if isinstance(output_tokens, int):
            metadata["output_tokens"] = output_tokens
        msg_count = sess["message_count"]
        if isinstance(msg_count, int):
            metadata["message_count"] = msg_count
        tool_count = sess["tool_call_count"]
        if isinstance(tool_count, int):
            metadata["tool_call_count"] = tool_count

        metadata["turn_count"] = len(turns)
        metadata["user_turns"] = sum(1 for t in turns if t.role == ROLE_USER)
        metadata["assistant_turns"] = sum(1 for t in turns if t.role == ROLE_ASSISTANT)

        if end_time and start_time:
            metadata["duration_minutes"] = round(
                max(0.0, (end_time - start_time).total_seconds() / 60.0),
                1,
            )

        return ObservedSession(
            session_id=session_id,
            source_path=db_path,
            start_time=start_time,
            end_time=end_time,
            turns=turns,
            metadata=metadata,
        )
