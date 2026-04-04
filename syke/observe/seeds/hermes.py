from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.observe.catalog import discovered_roots, get_source


def _default_source_roots() -> tuple[Path, ...]:
    spec = get_source("hermes")
    if spec is None:
        return ()
    return tuple(discovered_roots(spec))


_DB_FILENAME = "state.db"
_SESSION_FILE_RE = re.compile(r"^session_(?P<session_id>\d{8}_\d{6}_[0-9a-f]+)\.json$")
_REQUEST_DUMP_RE = re.compile(r"^request_dump_(?P<session_id>\d{8}_\d{6}_[0-9a-f]+)_.+\.json$")


class HermesObserveAdapter(ObserveAdapter):
    source = "hermes"

    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        source_roots: Iterable[Path | str] | None = None,
        data_dir: Path | str | None = None,
        source_db_path: Path | str | None = None,
    ):
        super().__init__(db, user_id)
        self._configured_source_roots = (
            tuple(Path(root).expanduser() for root in source_roots)
            if source_roots is not None
            else None
        )
        self.data_dir = Path(data_dir).expanduser() if data_dir is not None else None
        self.source_db_path = (
            Path(source_db_path).expanduser() if source_db_path is not None else None
        )

    def _source_roots(self) -> tuple[Path, ...]:
        if self._configured_source_roots is not None:
            return self._configured_source_roots

        derived: list[Path] = []
        for candidate in (self.data_dir, self.source_db_path):
            if candidate is None:
                continue
            derived.append(candidate if candidate.is_dir() else candidate.parent)
        if derived:
            return tuple(derived)
        return _default_source_roots()

    def discover(self) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()
        for root in self._source_roots():
            for path in self._expand_candidates(root, prefer_db=True):
                if path in seen:
                    continue
                seen.add(path)
                discovered.append(path)
        return sorted(discovered, key=self._candidate_sort_key)

    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]:
        explicit_paths = self._normalize_candidate_paths(paths)
        candidates = explicit_paths if explicit_paths is not None else self.discover()
        seen_session_ids: set[str] = set()

        for path in sorted(candidates, key=self._candidate_sort_key):
            if explicit_paths is None and since:
                try:
                    if path.stat().st_mtime < since:
                        continue
                except OSError:
                    continue

            iterator: Iterable[ObservedSession]
            if path.name == _DB_FILENAME:
                iterator = self._iter_sessions_from_db(
                    path,
                    since=since if explicit_paths is None else 0,
                )
            elif self._is_session_file(path):
                session = self._parse_session_json(path)
                iterator = () if session is None else (session,)
            elif self._is_request_dump_file(path):
                session = self._parse_request_dump(path)
                iterator = () if session is None else (session,)
            else:
                iterator = ()

            for session in iterator:
                if not session.turns or session.session_id in seen_session_ids:
                    continue
                if explicit_paths is None and since:
                    end_ts = (session.end_time or session.start_time).timestamp()
                    if end_ts < since:
                        continue
                seen_session_ids.add(session.session_id)
                yield session

    def _normalize_candidate_paths(self, paths: Iterable[Path] | None) -> list[Path] | None:
        if paths is None:
            return None

        normalized: list[Path] = []
        seen: set[Path] = set()
        for candidate in paths:
            if not isinstance(candidate, (str, Path)):
                continue
            for path in self._expand_candidates(Path(candidate).expanduser(), prefer_db=False):
                if path in seen:
                    continue
                seen.add(path)
                normalized.append(path)
        return sorted(normalized, key=self._candidate_sort_key)

    def _expand_candidates(self, candidate: Path, *, prefer_db: bool) -> list[Path]:
        try:
            resolved = candidate.resolve()
        except OSError:
            return []

        if resolved.is_file():
            if resolved.name == _DB_FILENAME:
                return [resolved]
            if self._is_session_file(resolved):
                return [resolved]
            if self._is_request_dump_file(resolved):
                return [resolved]
            return []

        if not resolved.is_dir():
            return []

        if prefer_db:
            db_path = resolved / _DB_FILENAME
            if db_path.is_file():
                try:
                    return [db_path.resolve()]
                except OSError:
                    return []

        if resolved.name == "sessions" or "sessions" in resolved.parts:
            return self._session_artifacts_in_dir(resolved)

        db_path = resolved / _DB_FILENAME
        if db_path.is_file():
            try:
                return [db_path.resolve()]
            except OSError:
                return []

        return []

    def _session_artifacts_in_dir(self, directory: Path) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()
        for path in directory.rglob("*.json"):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            if not (self._is_session_file(resolved) or self._is_request_dump_file(resolved)):
                continue
            seen.add(resolved)
            discovered.append(resolved)
        return sorted(discovered, key=self._candidate_sort_key)

    def _iter_sessions_from_db(self, db_path: Path, since: float = 0) -> Iterable[ObservedSession]:
        conn = self._connect_readonly(db_path)
        if conn is None:
            return

        try:
            for row in self._session_rows(conn, since):
                session = self._build_session_from_db(conn, db_path, row)
                if session is not None:
                    yield session
        finally:
            conn.close()

    def _session_rows(self, conn: sqlite3.Connection, since: float) -> Iterable[sqlite3.Row]:
        where = ""
        params: tuple[Any, ...] = ()
        if since:
            where = """
            WHERE s.started_at >= ?
               OR COALESCE(s.ended_at, 0) >= ?
               OR EXISTS (
                    SELECT 1
                    FROM messages m
                    WHERE m.session_id = s.id
                      AND m.timestamp >= ?
               )
            """
            params = (since, since, since)

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sessions)")
            if isinstance(row, sqlite3.Row) and row["name"]
        }
        wanted = [
            "id",
            "source",
            "user_id",
            "model",
            "model_config",
            "system_prompt",
            "parent_session_id",
            "started_at",
            "ended_at",
            "end_reason",
            "message_count",
            "tool_call_count",
            "input_tokens",
            "output_tokens",
            "title",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "billing_provider",
            "billing_base_url",
            "billing_mode",
            "estimated_cost_usd",
            "actual_cost_usd",
            "cost_status",
            "cost_source",
            "pricing_version",
        ]
        selected = [column for column in wanted if column in columns]
        query = f"""
        SELECT
            {", ".join(f"s.{column}" for column in selected)}
        FROM sessions s
        {where}
        ORDER BY s.started_at ASC, s.id ASC
        """
        yield from conn.execute(query, params)

    def _build_session_from_db(
        self,
        conn: sqlite3.Connection,
        db_path: Path,
        row: sqlite3.Row,
    ) -> ObservedSession | None:
        session_id = self._as_str(row["id"])
        if not session_id:
            return None

        message_rows = list(
            conn.execute(
                """
                SELECT
                    id,
                    session_id,
                    role,
                    content,
                    tool_call_id,
                    tool_calls,
                    tool_name,
                    timestamp,
                    token_count,
                    finish_reason,
                    reasoning,
                    reasoning_details,
                    codex_reasoning_items
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC, id ASC
                """,
                (session_id,),
            )
        )
        if not message_rows:
            return None

        turns: list[ObservedTurn] = []
        current_assistant_turn: ObservedTurn | None = None
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        start_time = self._parse_ts(row["started_at"])
        end_time = self._parse_ts(row["ended_at"])
        sequence = 0

        for message_row in message_rows:
            timestamp = self._parse_ts(message_row["timestamp"])
            if timestamp is None:
                timestamp = self._fallback_timestamp(start_time, sequence)
                sequence += 1

            start_time = timestamp if start_time is None or timestamp < start_time else start_time
            end_time = timestamp if end_time is None or timestamp > end_time else end_time

            role = self._as_str(message_row["role"])
            if role == "user":
                content = self._as_str(message_row["content"]) or ""
                if not content:
                    continue
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                        metadata={
                            "source_message_id": message_row["id"],
                            "source_event_type": "user",
                        },
                    )
                )
                current_assistant_turn = None
                continue

            if role == "assistant":
                current_assistant_turn = self._assistant_turn_from_message(
                    turns,
                    session_id,
                    message_row,
                    timestamp,
                    current_assistant_turn,
                    pending_tool_calls,
                )
                continue

            if role == "tool":
                current_assistant_turn = self._append_tool_result(
                    turns,
                    current_assistant_turn,
                    timestamp,
                    message_row,
                    pending_tool_calls,
                )

        if not turns or start_time is None:
            return None

        row_keys = set(row.keys())

        def _row_value(name: str) -> Any:
            return row[name] if name in row_keys else None

        metadata: dict[str, Any] = {
            "artifact_family": "sqlite",
            "source_root": str(db_path.parent),
            "source_type": self._as_str(_row_value("source")),
            "model": self._as_str(_row_value("model")),
            "title": self._as_str(_row_value("title")),
            "end_reason": self._as_str(_row_value("end_reason")),
            "billing_provider": self._as_str(_row_value("billing_provider")),
            "billing_base_url": self._as_str(_row_value("billing_base_url")),
            "billing_mode": self._as_str(_row_value("billing_mode")),
            "cost_status": self._as_str(_row_value("cost_status")),
            "cost_source": self._as_str(_row_value("cost_source")),
            "pricing_version": self._as_str(_row_value("pricing_version")),
            "message_count": self._as_int(_row_value("message_count")),
            "tool_call_count": self._as_int(_row_value("tool_call_count")),
            "input_tokens": self._as_int(_row_value("input_tokens")),
            "output_tokens": self._as_int(_row_value("output_tokens")),
            "cache_read_tokens": self._as_int(_row_value("cache_read_tokens")),
            "cache_write_tokens": self._as_int(_row_value("cache_write_tokens")),
            "reasoning_tokens": self._as_int(_row_value("reasoning_tokens")),
            "estimated_cost_usd": _row_value("estimated_cost_usd"),
            "actual_cost_usd": _row_value("actual_cost_usd"),
        }
        model_config = self._loads_json(_row_value("model_config"))
        if isinstance(model_config, dict):
            metadata["model_config"] = model_config

        return ObservedSession(
            session_id=session_id,
            source_path=db_path,
            start_time=start_time,
            end_time=end_time,
            parent_session_id=self._as_str(row["parent_session_id"]),
            turns=turns,
            metadata={key: value for key, value in metadata.items() if value is not None},
            is_subagent=self._as_str(row["parent_session_id"]) is not None,
            source_instance_id=f"{db_path}#{session_id}",
        )

    def _assistant_turn_from_message(
        self,
        turns: list[ObservedTurn],
        session_id: str,
        message_row: sqlite3.Row,
        timestamp: datetime,
        current: ObservedTurn | None,
        pending_tool_calls: dict[str, dict[str, Any]],
    ) -> ObservedTurn | None:
        content = self._compose_assistant_content(
            message_row["content"],
            message_row["reasoning"],
            message_row["reasoning_details"],
            message_row["codex_reasoning_items"],
        )
        tool_calls = self._parse_hermes_tool_calls(
            session_id,
            message_row["id"],
            message_row["tool_calls"],
        )
        if not content and not tool_calls:
            return current

        turn = ObservedTurn(
            role="assistant",
            content=content,
            timestamp=timestamp,
            metadata={
                "source_message_id": message_row["id"],
                "source_event_type": "assistant",
                "finish_reason": self._as_str(message_row["finish_reason"]),
                "token_count": self._as_int(message_row["token_count"]),
            },
        )
        for block in tool_calls:
            tool_id = self._as_str(block.get("tool_id"))
            if tool_id:
                pending_tool_calls[tool_id] = block
            turn.tool_calls.append(block)
        turns.append(turn)
        return turn

    def _append_tool_result(
        self,
        turns: list[ObservedTurn],
        current: ObservedTurn | None,
        timestamp: datetime,
        message_row: sqlite3.Row,
        pending_tool_calls: dict[str, dict[str, Any]],
    ) -> ObservedTurn:
        turn = current
        if turn is None:
            turn = ObservedTurn(
                role="assistant",
                content="",
                timestamp=timestamp,
                metadata={
                    "source_event_type": "tool_trace",
                    "source_message_id": message_row["id"],
                },
            )
            turns.append(turn)

        tool_call_id = self._as_str(message_row["tool_call_id"])
        tool_name = self._as_str(message_row["tool_name"])
        matched_call = pending_tool_calls.get(tool_call_id or "") if tool_call_id else None
        payload = self._loads_json(message_row["content"])

        result_block: dict[str, Any] = {
            "block_type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": self._tool_result_content(payload, message_row["content"]),
            "is_error": self._tool_result_is_error(payload),
        }
        if tool_name:
            result_block["tool_name"] = tool_name
        elif matched_call is not None:
            result_block["tool_name"] = matched_call.get("tool_name")
        turn.tool_calls.append(result_block)
        return turn

    def _parse_session_json(self, path: Path) -> ObservedSession | None:
        payload = self._load_json_file(path)
        if not isinstance(payload, dict):
            return None

        session_id = self._as_str(payload.get("session_id")) or self._session_id_from_filename(path)
        messages = payload.get("messages")
        if not session_id or not isinstance(messages, list):
            return None

        start_time = self._parse_ts(payload.get("session_start"))
        end_time = self._parse_ts(payload.get("last_updated"))
        turns: list[ObservedTurn] = []
        current_assistant_turn: ObservedTurn | None = None
        pending_tool_calls: dict[str, dict[str, Any]] = {}

        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            timestamp = self._fallback_timestamp(start_time, index)
            role = self._as_str(message.get("role"))

            if role == "user":
                content = self._as_str(message.get("content")) or ""
                if not content:
                    continue
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                        metadata={"source_event_type": "user", "source_message_index": index},
                    )
                )
                current_assistant_turn = None
                continue

            if role == "assistant":
                content = self._compose_assistant_content(
                    message.get("content"),
                    message.get("reasoning"),
                    None,
                    None,
                )
                tool_calls = self._parse_hermes_tool_calls(
                    session_id, index, message.get("tool_calls")
                )
                if not content and not tool_calls:
                    continue
                current_assistant_turn = ObservedTurn(
                    role="assistant",
                    content=content,
                    timestamp=timestamp,
                    metadata={
                        "source_event_type": "assistant",
                        "source_message_index": index,
                        "finish_reason": self._as_str(message.get("finish_reason")),
                    },
                )
                for block in tool_calls:
                    tool_id = self._as_str(block.get("tool_id"))
                    if tool_id:
                        pending_tool_calls[tool_id] = block
                    current_assistant_turn.tool_calls.append(block)
                turns.append(current_assistant_turn)
                continue

            if role == "tool":
                current_assistant_turn = self._append_tool_result_from_payload(
                    turns,
                    current_assistant_turn,
                    timestamp,
                    message,
                    pending_tool_calls,
                    source_index=index,
                )

        if not turns:
            return None

        if start_time is None:
            start_time = self._fallback_timestamp(None, 0)
        if end_time is None:
            end_time = self._fallback_timestamp(start_time, max(len(messages) - 1, 0))

        metadata: dict[str, Any] = {
            "artifact_family": "session_json",
            "source_root": str(path.parent),
            "model": self._as_str(payload.get("model")),
            "base_url": self._as_str(payload.get("base_url")),
            "platform": self._as_str(payload.get("platform")),
            "message_count": self._as_int(payload.get("message_count")),
        }
        tools = payload.get("tools")
        if isinstance(tools, list):
            metadata["tools_count"] = len(tools)

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            turns=turns,
            metadata={key: value for key, value in metadata.items() if value is not None},
            source_instance_id=str(path),
        )

    def _parse_request_dump(self, path: Path) -> ObservedSession | None:
        payload = self._load_json_file(path)
        if not isinstance(payload, dict):
            return None

        request = payload.get("request")
        if not isinstance(request, dict):
            return None
        body = request.get("body")
        if not isinstance(body, dict):
            return None

        session_id = self._as_str(payload.get("session_id")) or self._session_id_from_filename(path)
        messages = body.get("messages")
        if not session_id or not isinstance(messages, list):
            return None

        timestamp = self._parse_ts(payload.get("timestamp"))
        turns: list[ObservedTurn] = []
        current_assistant_turn: ObservedTurn | None = None
        pending_tool_calls: dict[str, dict[str, Any]] = {}

        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            turn_ts = self._fallback_timestamp(timestamp, index)
            role = self._as_str(message.get("role"))
            content = self._extract_request_message_text(message.get("content"))

            if role == "system":
                continue

            if role == "user":
                if not content:
                    continue
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=turn_ts,
                        metadata={
                            "source_event_type": "request_user",
                            "source_message_index": index,
                        },
                    )
                )
                current_assistant_turn = None
                continue

            if role == "assistant":
                tool_calls = self._parse_request_tool_calls(
                    session_id, index, message.get("tool_calls")
                )
                if not content and not tool_calls:
                    continue
                current_assistant_turn = ObservedTurn(
                    role="assistant",
                    content=content,
                    timestamp=turn_ts,
                    metadata={
                        "source_event_type": "request_assistant",
                        "source_message_index": index,
                    },
                )
                for block in tool_calls:
                    tool_id = self._as_str(block.get("tool_id"))
                    if tool_id:
                        pending_tool_calls[tool_id] = block
                    current_assistant_turn.tool_calls.append(block)
                turns.append(current_assistant_turn)
                continue

            if role == "tool":
                current_assistant_turn = self._append_tool_result_from_payload(
                    turns,
                    current_assistant_turn,
                    turn_ts,
                    message,
                    pending_tool_calls,
                    source_index=index,
                )

        if not turns:
            return None

        start_time = timestamp or self._fallback_timestamp(None, 0)
        metadata: dict[str, Any] = {
            "artifact_family": "request_dump",
            "source_root": str(path.parent),
            "reason": self._as_str(payload.get("reason")),
            "error": payload.get("error"),
            "request_url": self._as_str(request.get("url")),
            "request_method": self._as_str(request.get("method")),
            "model": self._as_str(body.get("model")),
        }
        tools = body.get("tools")
        if isinstance(tools, list):
            metadata["tools_count"] = len(tools)

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=self._fallback_timestamp(start_time, max(len(messages) - 1, 0)),
            turns=turns,
            metadata={key: value for key, value in metadata.items() if value is not None},
            source_instance_id=str(path),
        )

    def _append_tool_result_from_payload(
        self,
        turns: list[ObservedTurn],
        current: ObservedTurn | None,
        timestamp: datetime,
        payload: dict[str, Any],
        pending_tool_calls: dict[str, dict[str, Any]],
        *,
        source_index: int,
    ) -> ObservedTurn:
        turn = current
        if turn is None:
            turn = ObservedTurn(
                role="assistant",
                content="",
                timestamp=timestamp,
                metadata={
                    "source_event_type": "tool_trace",
                    "source_message_index": source_index,
                },
            )
            turns.append(turn)

        tool_call_id = self._as_str(payload.get("tool_call_id"))
        matched_call = pending_tool_calls.get(tool_call_id or "") if tool_call_id else None
        content = payload.get("content")
        parsed = self._loads_json(content)
        result_block: dict[str, Any] = {
            "block_type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": self._tool_result_content(parsed, content),
            "is_error": self._tool_result_is_error(parsed),
        }
        if matched_call is not None:
            result_block["tool_name"] = matched_call.get("tool_name")
        turn.tool_calls.append(result_block)
        return turn

    def _parse_hermes_tool_calls(
        self,
        session_id: str,
        source_id: int,
        value: Any,
    ) -> list[dict[str, Any]]:
        parsed = self._loads_json(value) if isinstance(value, str) else value
        if not isinstance(parsed, list):
            return []

        tool_calls: list[dict[str, Any]] = []
        for offset, item in enumerate(parsed):
            if not isinstance(item, dict):
                continue
            function = item.get("function")
            tool_name = None
            arguments = None
            if isinstance(function, dict):
                tool_name = self._as_str(function.get("name"))
                arguments = self._maybe_parse_json(function.get("arguments"))
            tool_name = (
                tool_name or self._as_str(item.get("name")) or self._as_str(item.get("type"))
            )
            tool_id = (
                self._as_str(item.get("call_id"))
                or self._as_str(item.get("id"))
                or f"{session_id}:tool:{source_id}:{offset}"
            )
            block: dict[str, Any] = {
                "block_type": "tool_use",
                "tool_name": tool_name or "tool",
                "tool_id": tool_id,
                "input": arguments if arguments is not None else {},
            }
            tool_type = self._as_str(item.get("type"))
            if tool_type:
                block["tool_kind"] = tool_type
            tool_calls.append(block)
        return tool_calls

    def _parse_request_tool_calls(
        self,
        session_id: str,
        source_index: int,
        value: Any,
    ) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return self._parse_hermes_tool_calls(session_id, source_index, value)
        return []

    def _compose_assistant_content(
        self,
        content: Any,
        reasoning: Any,
        reasoning_details: Any,
        codex_reasoning_items: Any,
    ) -> str:
        pieces: list[str] = []
        text = self._as_str(content)
        if text:
            pieces.append(text)

        reasoning_text = self._as_str(reasoning)
        if reasoning_text:
            pieces.append(f"[reasoning]\n{reasoning_text}")

        details = self._loads_json(reasoning_details)
        detail_text = self._stringify_reasoning_details(details)
        if detail_text:
            pieces.append(f"[reasoning_details]\n{detail_text}")

        codex_items = self._loads_json(codex_reasoning_items)
        codex_text = self._stringify_reasoning_details(codex_items)
        if codex_text:
            pieces.append(f"[codex_reasoning]\n{codex_text}")

        return "\n\n".join(piece for piece in pieces if piece)

    def _tool_result_content(self, payload: Any, raw: Any) -> str:
        if isinstance(payload, dict):
            output = payload.get("output")
            if isinstance(output, str):
                return output
        return self._stringify(payload if payload is not None else raw)

    def _tool_result_is_error(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        exit_code = payload.get("exit_code")
        if isinstance(exit_code, (int, float)) and int(exit_code) != 0:
            return True
        error = payload.get("error")
        if error not in {None, "", False}:
            return True
        success = payload.get("success")
        if isinstance(success, bool):
            return not success
        return False

    def _extract_request_message_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return ""

        pieces: list[str] = []
        for item in value:
            if isinstance(item, str):
                pieces.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = self._as_str(item.get("text"))
            if text:
                pieces.append(text)
                continue
            item_type = self._as_str(item.get("type"))
            if item_type:
                pieces.append(f"[{item_type}]")
        return "\n\n".join(piece for piece in pieces if piece)

    def _load_json_file(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _connect_readonly(self, db_path: Path) -> sqlite3.Connection | None:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        conn.row_factory = sqlite3.Row
        return conn

    def _candidate_sort_key(self, path: Path) -> tuple[int, str]:
        if path.name == _DB_FILENAME:
            priority = 0
        elif self._is_session_file(path):
            priority = 1
        elif self._is_request_dump_file(path):
            priority = 2
        else:
            priority = 99
        return priority, str(path)

    def _is_session_file(self, path: Path) -> bool:
        return path.suffix == ".json" and bool(_SESSION_FILE_RE.match(path.name))

    def _is_request_dump_file(self, path: Path) -> bool:
        return path.suffix == ".json" and bool(_REQUEST_DUMP_RE.match(path.name))

    def _session_id_from_filename(self, path: Path) -> str:
        match = _SESSION_FILE_RE.match(path.name) or _REQUEST_DUMP_RE.match(path.name)
        if match:
            return match.group("session_id")
        return path.stem

    @staticmethod
    def _fallback_timestamp(base: datetime | None, sequence: int) -> datetime:
        origin = base or datetime.fromtimestamp(0, tz=UTC)
        return origin + timedelta(microseconds=sequence)

    @staticmethod
    def _loads_json(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _maybe_parse_json(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)

    @staticmethod
    def _stringify_reasoning_details(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            pieces: list[str] = []
            for item in value:
                if isinstance(item, str):
                    pieces.append(item)
                    continue
                if isinstance(item, dict):
                    text = HermesObserveAdapter._as_str(item.get("text"))
                    if text:
                        pieces.append(text)
                        continue
                pieces.append(HermesObserveAdapter._stringify(item))
            return "\n".join(piece for piece in pieces if piece)
        return HermesObserveAdapter._stringify(value)

    @staticmethod
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return None

    @staticmethod
    def _parse_ts(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp <= 0:
                return None
            if timestamp > 1e18:
                timestamp /= 1e9
            elif timestamp > 1e15:
                timestamp /= 1e6
            elif timestamp > 1e12:
                timestamp /= 1e3
            return datetime.fromtimestamp(timestamp, tz=UTC)
        return None
