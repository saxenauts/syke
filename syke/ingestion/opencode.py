from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast, override

from syke.config_file import expand_path
from syke.ingestion.constants import ROLE_ASSISTANT, ROLE_USER
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn

logger = logging.getLogger(__name__)


class OpenCodeAdapter(ObserveAdapter):
    source: str = "opencode"

    _DB_PATH: str = "~/.local/share/opencode/opencode.db"

    @override
    def discover(self) -> list[Path]:
        db_path = expand_path(self._DB_PATH)
        if not db_path.exists() or not db_path.is_file():
            return []

        last_sync = self._sync_epoch()
        if last_sync and db_path.stat().st_mtime < last_sync:
            return []

        return [db_path]

    @override
    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        paths = self.discover()
        if not paths:
            return

        db_path = paths[0]
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            logger.warning("Cannot open OpenCode DB at %s: %s", db_path, exc)
            return

        try:
            yield from self._read_sessions(conn, db_path, since)
        finally:
            conn.close()

    def _read_sessions(
        self,
        conn: sqlite3.Connection,
        db_path: Path,
        since: float,
    ) -> Iterable[ObservedSession]:
        cutoff = since if since > 0 else self._sync_epoch()
        cutoff_ms = int(cutoff * 1000) if cutoff > 0 else 0

        if cutoff_ms > 0:
            rows = conn.execute(
                """
                SELECT *
                FROM session
                WHERE time_updated >= ?
                ORDER BY time_created, id
                """,
                (cutoff_ms,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM session ORDER BY time_created, id").fetchall()

        for sess in rows:
            try:
                session = self._parse_session(conn, sess, db_path)
                if session is not None:
                    yield session
            except Exception as exc:
                logger.warning("Failed to parse OpenCode session %s: %s", sess["id"], exc)

    def _parse_session(
        self,
        conn: sqlite3.Connection,
        sess: sqlite3.Row,
        db_path: Path,
    ) -> ObservedSession | None:
        session_id_obj = sess["id"]
        if not isinstance(session_id_obj, str) or not session_id_obj:
            return None
        session_id = session_id_obj

        start_time = self._ts_from_epoch_ms(sess["time_created"]) or datetime.now(UTC)
        end_time = self._ts_from_epoch_ms(sess["time_updated"])

        message_rows = conn.execute(
            """
            SELECT id, time_created, data
            FROM message
            WHERE session_id = ?
            ORDER BY time_created, id
            """,
            (session_id,),
        ).fetchall()

        turns: list[ObservedTurn] = []
        for idx, msg in enumerate(message_rows):
            message_id_obj = msg["id"]
            if not isinstance(message_id_obj, str) or not message_id_obj:
                continue
            message_id = message_id_obj

            payload = self._parse_json_object(msg["data"])
            if payload is None:
                continue

            role_raw = payload.get("role")
            if role_raw not in {ROLE_USER, ROLE_ASSISTANT}:
                continue
            role = ROLE_USER if role_raw == ROLE_USER else ROLE_ASSISTANT

            parts = self._load_parts(conn, message_id)
            content = self._extract_message_content(parts)
            tool_calls = self._extract_tool_blocks(parts)

            timestamp = self._extract_message_timestamp(payload)
            if timestamp is None:
                timestamp = self._ts_from_epoch_ms(msg["time_created"]) or start_time

            turn_metadata: dict[str, Any] = {
                "source_line_index": idx,
                "source_event_type": "message",
            }
            for key in ("modelID", "providerID", "mode", "agent", "variant", "path"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    turn_metadata[self._snake_case_key(key)] = value

            stop_reason = payload.get("stopReason") or payload.get("finish")
            if isinstance(stop_reason, str) and stop_reason:
                turn_metadata["stop_reason"] = stop_reason

            tokens_obj = payload.get("tokens")
            usage = self._extract_usage(tokens_obj)
            if usage:
                turn_metadata["usage"] = usage

            turns.append(
                ObservedTurn(
                    role=role,
                    content=content,
                    timestamp=timestamp,
                    tool_calls=tool_calls,
                    metadata=turn_metadata,
                )
            )

        metadata: dict[str, object] = {
            "turn_count": len(turns),
            "user_turns": sum(1 for turn in turns if turn.role == ROLE_USER),
            "assistant_turns": sum(1 for turn in turns if turn.role == ROLE_ASSISTANT),
            "message_count": len(message_rows),
        }

        for db_key, meta_key in (
            ("title", "title"),
            ("slug", "slug"),
            ("version", "version"),
            ("project_id", "project_id"),
            ("workspace_id", "workspace_id"),
            ("share_url", "share_url"),
            ("permission", "permission"),
        ):
            value = sess[db_key]
            if isinstance(value, str) and value:
                metadata[meta_key] = value

        for db_key, meta_key in (
            ("summary_additions", "summary_additions"),
            ("summary_deletions", "summary_deletions"),
            ("summary_files", "summary_files"),
        ):
            value = sess[db_key]
            if isinstance(value, int):
                metadata[meta_key] = value

        for db_key, meta_key in (
            ("summary_diffs", "summary_diffs"),
            ("revert", "revert"),
        ):
            value = sess[db_key]
            if value is not None:
                metadata[meta_key] = value

        if end_time is not None:
            metadata["duration_minutes"] = round(
                max(0.0, (end_time - start_time).total_seconds() / 60.0),
                1,
            )

        directory = sess["directory"]
        project: str | None = None
        if isinstance(directory, str) and directory:
            project = self._normalize_project_path(directory)
            metadata["directory"] = directory

        parent_id = sess["parent_id"]
        parent_session_id = parent_id if isinstance(parent_id, str) and parent_id else None

        return ObservedSession(
            session_id=session_id,
            source_path=db_path,
            start_time=start_time,
            end_time=end_time,
            project=project,
            parent_session_id=parent_session_id,
            turns=turns,
            metadata=metadata,
        )

    def _load_parts(self, conn: sqlite3.Connection, message_id: str) -> list[dict[str, object]]:
        rows = conn.execute(
            """
            SELECT data
            FROM part
            WHERE message_id = ?
            ORDER BY time_created, id
            """,
            (message_id,),
        ).fetchall()

        parts: list[dict[str, object]] = []
        for row in rows:
            parsed = self._parse_json_object(row["data"])
            if parsed is not None:
                parts.append(parsed)
        return parts

    def _extract_message_content(self, parts: list[dict[str, object]]) -> str:
        text_blocks: list[str] = []
        reasoning_blocks: list[str] = []

        for part in parts:
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if isinstance(text, str) and text:
                    text_blocks.append(self._safe_text(text))
            elif part_type == "reasoning":
                text = part.get("text")
                if isinstance(text, str) and text:
                    reasoning_blocks.append(self._safe_text(text))

        content_blocks = reasoning_blocks + text_blocks
        return "\n".join(content_blocks)

    def _extract_tool_blocks(self, parts: list[dict[str, object]]) -> list[dict[str, object]]:
        tool_calls: list[dict[str, object]] = []

        for idx, part in enumerate(parts):
            if part.get("type") != "tool":
                continue

            tool_name_obj = part.get("tool")
            tool_name = (
                tool_name_obj if isinstance(tool_name_obj, str) and tool_name_obj else "tool"
            )

            call_id_obj = part.get("callID")
            call_id = (
                call_id_obj
                if isinstance(call_id_obj, str) and call_id_obj
                else f"tool-{idx}-{tool_name}"
            )

            state_obj = part.get("state")
            state = cast(dict[str, object], state_obj) if isinstance(state_obj, dict) else {}
            input_obj = state.get("input")
            tool_input = cast(dict[str, object], input_obj) if isinstance(input_obj, dict) else {}

            tool_calls.append(
                {
                    "block_type": "tool_use",
                    "tool_name": tool_name,
                    "tool_id": call_id,
                    "input": tool_input,
                }
            )

            status_obj = state.get("status")
            status = status_obj if isinstance(status_obj, str) else ""
            output_obj = state.get("output")
            error_obj = state.get("error")

            has_terminal_state = status in {"completed", "failed", "error", "cancelled"}
            if not has_terminal_state and output_obj is None and error_obj is None:
                continue

            result_content = self._stringify_tool_result(output_obj, error_obj)
            is_error = status in {"failed", "error"} or error_obj is not None

            tool_calls.append(
                {
                    "block_type": "tool_result",
                    "tool_use_id": call_id,
                    "content": result_content,
                    "is_error": is_error,
                }
            )

        return tool_calls

    @staticmethod
    def _parse_json_object(value: object) -> dict[str, object] | None:
        if isinstance(value, dict):
            return cast(dict[str, object], value)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return cast(dict[str, object], parsed)
        return None

    @staticmethod
    def _stringify_tool_result(output: object, error: object) -> str:
        if error is not None:
            if isinstance(error, str):
                return OpenCodeAdapter._safe_text(error)
            return OpenCodeAdapter._safe_text(json.dumps(error, default=str))

        if output is None:
            return ""
        if isinstance(output, str):
            return OpenCodeAdapter._safe_text(output)
        return OpenCodeAdapter._safe_text(json.dumps(output, default=str))

    @staticmethod
    def _safe_text(value: str) -> str:
        return value.encode("utf-8", "backslashreplace").decode("utf-8")

    @staticmethod
    def _extract_message_timestamp(payload: dict[str, object]) -> datetime | None:
        time_obj = payload.get("time")
        if not isinstance(time_obj, dict):
            return None

        created_obj = time_obj.get("created")
        if isinstance(created_obj, (int, float)):
            return OpenCodeAdapter._ts_from_epoch_ms(created_obj)
        return None

    @staticmethod
    def _extract_usage(tokens_obj: object) -> dict[str, object] | None:
        if not isinstance(tokens_obj, dict):
            return None

        usage: dict[str, object] = {}
        input_tokens = tokens_obj.get("input")
        output_tokens = tokens_obj.get("output")
        if isinstance(input_tokens, int):
            usage["input_tokens"] = input_tokens
        if isinstance(output_tokens, int):
            usage["output_tokens"] = output_tokens

        cache_obj = tokens_obj.get("cache")
        if isinstance(cache_obj, dict):
            cache_read = cache_obj.get("read")
            cache_write = cache_obj.get("write")
            if isinstance(cache_read, int):
                usage["cache_read_input_tokens"] = cache_read
            if isinstance(cache_write, int):
                usage["cache_creation_input_tokens"] = cache_write

        reasoning_tokens = tokens_obj.get("reasoning")
        if isinstance(reasoning_tokens, int):
            usage["reasoning_tokens"] = reasoning_tokens

        return usage or None

    @staticmethod
    def _snake_case_key(key: str) -> str:
        chars: list[str] = []
        for idx, char in enumerate(key):
            if char.isupper() and idx > 0:
                chars.append("_")
            chars.append(char.lower())
        return "".join(chars)

    @staticmethod
    def _ts_from_epoch_ms(value: object) -> datetime | None:
        if not isinstance(value, (int, float)):
            return None
        try:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _normalize_project_path(directory: str) -> str:
        home = str(Path.home())
        if directory.startswith(home + "/"):
            return "~/" + directory[len(home) + 1 :]
        if directory == home:
            return "~"
        return directory

    def _sync_epoch(self) -> float:
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        if not last_sync:
            return 0.0
        try:
            return datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp()
        except ValueError:
            return 0.0
