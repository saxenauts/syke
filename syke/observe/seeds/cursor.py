from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.observe.catalog import discovered_roots, get_source


def _default_source_roots() -> tuple[Path, ...]:
    spec = get_source("cursor")
    if spec is None:
        return ()
    return tuple(discovered_roots(spec))


_KEY_HINT_RE = re.compile(r"(composerData|chat|conversation|session)", re.IGNORECASE)


class CursorObserveAdapter(ObserveAdapter):
    source = "cursor"

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
            for path in self._expand_candidates(root):
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
            session = (
                self._parse_state_db(path)
                if path.name.startswith("state.vscdb")
                else self._parse_json_session(path)
            )
            if session is None or not session.turns or session.session_id in seen_session_ids:
                continue
            if explicit_paths is None and since:
                end_ts = (session.end_time or session.start_time).timestamp()
                if end_ts < since:
                    continue
            seen_session_ids.add(session.session_id)
            yield session

    def _candidate_sort_key(self, path: Path) -> tuple[int, str]:
        score = 0 if path.name.startswith("state.vscdb") else 1
        return (score, str(path))

    def _normalize_candidate_paths(self, paths: Iterable[Path] | None) -> list[Path] | None:
        if paths is None:
            return None
        normalized: list[Path] = []
        seen: set[Path] = set()
        for candidate in paths:
            if not isinstance(candidate, (str, Path)):
                continue
            for path in self._expand_candidates(Path(candidate).expanduser()):
                if path in seen:
                    continue
                seen.add(path)
                normalized.append(path)
        return sorted(normalized, key=self._candidate_sort_key)

    def _expand_candidates(self, candidate: Path) -> list[Path]:
        try:
            resolved = candidate.resolve()
        except OSError:
            return []

        if resolved.is_file():
            return [resolved] if self._is_supported_file(resolved) else []

        if not resolved.is_dir():
            return []

        results: list[Path] = []
        for child in resolved.rglob("*"):
            try:
                child_resolved = child.resolve()
            except OSError:
                continue
            if child_resolved.is_file() and self._is_supported_file(child_resolved):
                results.append(child_resolved)
        return results

    def _is_supported_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.name.startswith("state.vscdb"):
            return True
        return path.suffix in {".json", ".jsonl"} and "chatSessions" in path.parts

    def _parse_state_db(self, db_path: Path) -> ObservedSession | None:
        conn = self._connect_readonly(db_path)
        if conn is None:
            return None

        try:
            tables = [
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                if isinstance(row, sqlite3.Row) and row["name"]
            ]
            for table in tables:
                session = self._parse_state_table(conn, db_path, table)
                if session is not None:
                    return session
        finally:
            conn.close()
        return None

    def _parse_state_table(
        self,
        conn: sqlite3.Connection,
        db_path: Path,
        table: str,
    ) -> ObservedSession | None:
        columns = [
            row["name"]
            for row in conn.execute(f'PRAGMA table_info("{table}")')
            if isinstance(row, sqlite3.Row) and row["name"]
        ]
        if not columns:
            return None

        key_column = next(
            (column for column in columns if column.lower() in {"key", "itemkey", "name"}),
            None,
        )
        value_column = next(
            (
                column
                for column in columns
                if column.lower() in {"value", "itemvalue", "data", "json", "blob"}
            ),
            None,
        )
        if value_column is None:
            return None

        select_columns = [value_column]
        if key_column is not None:
            select_columns.insert(0, key_column)
        quoted_columns = ", ".join(f'"{column}"' for column in select_columns)
        query = f'SELECT {quoted_columns} FROM "{table}"'

        for row in conn.execute(query):
            raw_key = row[key_column] if key_column is not None else None
            if isinstance(raw_key, str) and not _KEY_HINT_RE.search(raw_key):
                continue
            raw_value = row[value_column]
            payload = self._decode_json_blob(raw_value)
            if payload is None:
                continue
            session = self._session_from_blob(
                payload,
                source_path=db_path,
                session_id_hint=self._session_hint_from_key(raw_key),
            )
            if session is not None:
                session.metadata["state_table"] = table
                if raw_key:
                    session.metadata["state_key"] = raw_key
                return session
        return None

    def _parse_json_session(self, path: Path) -> ObservedSession | None:
        payload = self._loads_json(path)
        if payload is None:
            return None
        return self._session_from_blob(payload, source_path=path, session_id_hint=path.stem)

    def _session_from_blob(
        self,
        payload: Any,
        *,
        source_path: Path,
        session_id_hint: str | None,
    ) -> ObservedSession | None:
        payload_dict = payload if isinstance(payload, dict) else None
        messages = self._extract_messages(payload)
        if not messages:
            return None

        session_id = (
            self._as_str(payload_dict.get("sessionId")) if payload_dict else None
        ) or (
            self._as_str(payload_dict.get("composerId")) if payload_dict else None
        ) or (
            self._as_str(payload_dict.get("conversationId")) if payload_dict else None
        ) or (
            self._as_str(payload_dict.get("id")) if payload_dict else None
        ) or session_id_hint or source_path.stem

        turns: list[ObservedTurn] = []
        times: list[datetime] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            role = self._normalize_role(message.get("role") or message.get("type") or message.get("author"))
            if role not in {"user", "assistant"}:
                continue
            timestamp = self._parse_ts(
                message.get("timestamp")
                or message.get("createdAt")
                or message.get("updatedAt")
                or message.get("time")
            ) or self._file_timestamp(source_path)
            if timestamp is None:
                continue
            content = self._extract_text(
                message.get("text")
                or message.get("content")
                or message.get("message")
                or message.get("body")
            )
            if not content and role == "user":
                continue
            turn = ObservedTurn(
                role=role,
                content=content,
                timestamp=timestamp.replace(microsecond=index),
                metadata={"artifact_family": "cursor_session_blob", "source_index": index},
            )
            tool_calls = message.get("toolCalls") or message.get("tools")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_id = self._as_str(tool_call.get("id")) or f"{session_id}:{index}"
                    turn.tool_calls.append(
                        {
                            "block_type": "tool_use",
                            "tool_name": self._as_str(tool_call.get("name")),
                            "tool_id": tool_id,
                            "input": tool_call.get("args") or tool_call.get("input") or {},
                        }
                    )
                    if tool_call.get("result") is not None:
                        turn.tool_calls.append(
                            {
                                "block_type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": self._extract_text(tool_call.get("result")),
                                "is_error": False,
                            }
                        )
            turns.append(turn)
            times.append(turn.timestamp)

        if not turns or not times:
            return None

        return ObservedSession(
            session_id=session_id,
            source_path=source_path,
            start_time=min(times),
            end_time=max(times),
            turns=turns,
            metadata={"artifact_family": "cursor_session"},
        )

    def _extract_messages(self, payload: Any) -> list[Any]:
        if isinstance(payload, dict):
            for key in ("messages", "conversation", "chat"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    return nested
                if isinstance(nested, dict):
                    nested_messages = nested.get("messages")
                    if isinstance(nested_messages, list):
                        return nested_messages
            for key in ("tabs", "entries", "sessions"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        extracted = self._extract_messages(item)
                        if extracted:
                            return extracted
        elif isinstance(payload, list):
            if payload and all(isinstance(item, dict) for item in payload):
                return payload
        return []

    def _decode_json_blob(self, value: Any) -> Any:
        if isinstance(value, bytes):
            for encoding in ("utf-8", "utf-16"):
                try:
                    value = value.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return None
        return self._loads_json(value)

    def _session_hint_from_key(self, value: Any) -> str | None:
        text = self._as_str(value)
        if text is None:
            return None
        parts = re.split(r"[:/]", text)
        return next((part for part in reversed(parts) if part), None)

    def _normalize_role(self, value: Any) -> str | None:
        text = self._as_str(value)
        if text is None:
            return None
        lowered = text.lower()
        if lowered in {"user", "human"}:
            return "user"
        if lowered in {"assistant", "model", "ai"}:
            return "assistant"
        return None

    def _extract_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("text", "content", "message", "body", "output", "response"):
                if isinstance(value.get(key), str):
                    return value[key]
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            parts = [self._extract_text(item) for item in value]
            return "\n".join(part for part in parts if part).strip()
        return str(value)

    def _loads_json(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, Path):
            try:
                return json.loads(value.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        if not isinstance(value, str) or not value.strip():
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    def _connect_readonly(self, db_path: Path) -> sqlite3.Connection | None:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        conn.row_factory = sqlite3.Row
        return conn

    def _parse_ts(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            if float(value) > 1_000_000_000_000:
                return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
            return datetime.fromtimestamp(float(value), tz=UTC)
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _file_timestamp(self, path: Path) -> datetime | None:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            return None

    def _as_str(self, value: Any) -> str | None:
        return value if isinstance(value, str) and value else None
