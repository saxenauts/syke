from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

_DEFAULT_SOURCE_ROOTS = (
    Path("~/.copilot/session-state").expanduser(),
    Path("~/Library/Application Support/Code/User/workspaceStorage").expanduser(),
    Path("~/Library/Application Support/Code/User/globalStorage").expanduser(),
)


class CopilotObserveAdapter(ObserveAdapter):
    source = "copilot"

    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        source_roots: Iterable[Path | str] | None = None,
    ):
        super().__init__(db, user_id)
        self._configured_source_roots = (
            tuple(Path(root).expanduser() for root in source_roots)
            if source_roots is not None
            else None
        )

    def _source_roots(self) -> tuple[Path, ...]:
        return self._configured_source_roots or _DEFAULT_SOURCE_ROOTS

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

        for path in sorted(candidates, key=self._candidate_sort_key):
            if explicit_paths is None and since:
                try:
                    if path.stat().st_mtime < since:
                        continue
                except OSError:
                    continue

            session = self._parse_candidate(path)
            if session is None or not session.turns:
                continue

            if explicit_paths is None and since:
                end_ts = (session.end_time or session.start_time).timestamp()
                if end_ts < since:
                    continue

            yield session

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
            if resolved.name == "events.jsonl":
                return [resolved]
            if self._is_vscode_chat_session_file(resolved):
                return [resolved]
            if resolved.name == "workspace.yaml":
                sibling = resolved.with_name("events.jsonl")
                if sibling.is_file():
                    try:
                        return [sibling.resolve()]
                    except OSError:
                        return []
            return []

        if not resolved.is_dir():
            return []

        results: list[Path] = []
        if resolved.name == "session-state":
            for session_dir in resolved.iterdir():
                events_path = session_dir / "events.jsonl"
                if events_path.is_file():
                    try:
                        results.append(events_path.resolve())
                    except OSError:
                        continue
        else:
            for child in resolved.rglob("events.jsonl"):
                try:
                    results.append(child.resolve())
                except OSError:
                    continue
            for suffix in ("*.json", "*.jsonl"):
                for child in resolved.rglob(suffix):
                    try:
                        child_resolved = child.resolve()
                    except OSError:
                        continue
                    if self._is_vscode_chat_session_file(child_resolved):
                        results.append(child_resolved)

        deduped: list[Path] = []
        seen: set[Path] = set()
        for path in results:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    def _candidate_sort_key(self, path: Path) -> tuple[int, str]:
        return (0 if path.name == "events.jsonl" else 1, str(path))

    def _is_vscode_chat_session_file(self, path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
            return False
        parts = {part.lower() for part in path.parts}
        return "chatsessions" in parts or "emptywindowchatsessions" in parts

    def _parse_candidate(self, path: Path) -> ObservedSession | None:
        if path.name == "events.jsonl":
            return self._parse_cli_session(path)
        return self._parse_vscode_chat_session(path)

    def _parse_cli_session(self, path: Path) -> ObservedSession | None:
        session_dir = path.parent
        session_id = session_dir.name
        workspace_meta = self._load_workspace_yaml(session_dir / "workspace.yaml")
        store_meta = self._load_session_store_metadata(session_dir, session_id)

        turns: list[ObservedTurn] = []
        start_time: datetime | None = None
        end_time: datetime | None = None
        pending_assistant: ObservedTurn | None = None
        fallback_base = self._path_timestamp(path)

        for line_index, record in self._iter_jsonl(path):
            timestamp = self._record_timestamp(record) or (
                fallback_base + timedelta(microseconds=line_index)
            )
            role = self._record_role(record)
            content = self._record_text(record)

            if role == "user" and content:
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                        metadata=self._compact_dict(
                            {
                                "source_event_type": self._as_str(record.get("type"))
                                or self._as_str(record.get("event"))
                                or "user_event",
                                "source_line_index": line_index,
                            }
                        ),
                    )
                )
                pending_assistant = None
            elif role == "assistant" and content:
                pending_assistant = ObservedTurn(
                    role="assistant",
                    content=content,
                    timestamp=timestamp,
                    metadata=self._compact_dict(
                        {
                            "source_event_type": self._as_str(record.get("type"))
                            or self._as_str(record.get("event"))
                            or "assistant_event",
                            "source_line_index": line_index,
                            "model": self._as_str(record.get("model")),
                        }
                    ),
                )
                turns.append(pending_assistant)
            elif self._looks_like_tool_event(record) and pending_assistant is not None:
                tool_block = self._tool_block_from_record(record, line_index, session_id)
                if tool_block is not None:
                    pending_assistant.tool_calls.append(tool_block)

            start_time = timestamp if start_time is None or timestamp < start_time else start_time
            end_time = timestamp if end_time is None or timestamp > end_time else end_time

        if not turns or start_time is None:
            return None

        metadata = self._compact_dict(
            {
                "artifact_family": "copilot_cli_events",
                "source_root": str(session_dir),
                "workspace_yaml_path": str(session_dir / "workspace.yaml")
                if (session_dir / "workspace.yaml").is_file()
                else None,
                "workspace_context": workspace_meta or None,
                "session_store": store_meta or None,
            }
        )

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            project=self._workspace_project_from_yaml(workspace_meta),
            turns=turns,
            metadata=metadata,
            source_instance_id=str(path),
        )

    def _parse_vscode_chat_session(self, path: Path) -> ObservedSession | None:
        data = self._load_json(path)
        if not isinstance(data, dict):
            return None

        requests = data.get("requests")
        if not isinstance(requests, list):
            return None

        turns: list[ObservedTurn] = []
        creation_ts = self._parse_ts(data.get("creationDate")) or self._path_timestamp(path)
        last_message_ts = self._parse_ts(data.get("lastMessageDate")) or creation_ts

        for index, request in enumerate(requests):
            if not isinstance(request, dict):
                continue
            base_ts = self._parse_ts(request.get("timestamp")) or (
                creation_ts + timedelta(microseconds=index * 2)
            )

            user_text = self._request_message_text(request.get("message"))
            if user_text:
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=user_text,
                        timestamp=base_ts,
                        metadata=self._compact_dict(
                            {
                                "source_event_type": "request",
                                "source_request_id": self._as_str(request.get("requestId")),
                                "source_request_index": index,
                            }
                        ),
                    )
                )

            assistant_text = self._response_text(request.get("response"))
            tool_calls = self._response_tool_calls(request)
            if assistant_text or tool_calls:
                turns.append(
                    ObservedTurn(
                        role="assistant",
                        content=assistant_text,
                        timestamp=base_ts + timedelta(microseconds=1),
                        tool_calls=tool_calls,
                        metadata=self._compact_dict(
                            {
                                "source_event_type": "response",
                                "source_request_id": self._as_str(request.get("requestId")),
                                "response_id": self._as_str(request.get("responseId")),
                                "model_id": self._as_str(request.get("modelId")),
                            }
                        ),
                    )
                )

        if not turns:
            return None

        metadata = self._compact_dict(
            {
                "artifact_family": "vscode_chat_session",
                "source_root": str(path.parent),
                "title": self._as_str(data.get("customTitle"))
                or self._as_str(data.get("computedTitle")),
                "version": data.get("version"),
                "initial_location": data.get("initialLocation"),
                "last_message_date": self._format_ts(last_message_ts),
            }
        )

        return ObservedSession(
            session_id=self._as_str(data.get("sessionId")) or path.stem,
            source_path=path,
            start_time=min(turn.timestamp for turn in turns),
            end_time=max(turn.timestamp for turn in turns),
            project=self._workspace_project_from_path(path),
            turns=turns,
            metadata=metadata,
            source_instance_id=str(path),
        )

    def _load_workspace_yaml(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}

        parsed: dict[str, Any] = {}
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        parsed[key] = value
        except OSError:
            return {}
        return parsed

    def _workspace_project_from_yaml(self, metadata: dict[str, Any]) -> str | None:
        for key in ("workspace", "workspace_path", "root", "cwd", "path"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _workspace_project_from_path(self, path: Path) -> str | None:
        for parent in (path.parent, *path.parents):
            workspace_json = parent / "workspace.json"
            if not workspace_json.is_file():
                continue
            data = self._load_json(workspace_json)
            if not isinstance(data, dict):
                continue
            folder = self._as_str(data.get("folder"))
            if folder and folder.startswith("file://"):
                return folder.removeprefix("file://")
            workspace = self._as_str(data.get("workspace"))
            if workspace:
                return workspace
        return None

    def _load_session_store_metadata(
        self,
        session_dir: Path,
        session_id: str,
    ) -> dict[str, Any]:
        store_path = session_dir.parent.parent / "session-store.db"
        if not store_path.is_file():
            return {}

        metadata: dict[str, Any] = {"path": str(store_path)}
        try:
            with sqlite3.connect(f"file:{store_path}?mode=ro", uri=True) as conn:
                tables = [
                    row[0]
                    for row in conn.execute(
                        "select name from sqlite_master where type = 'table' order by name"
                    )
                    if isinstance(row[0], str)
                ]
                metadata["tables"] = tables
                for table in tables:
                    row_meta = self._session_store_row(conn, table, session_id)
                    if row_meta:
                        metadata.setdefault("rows", {})[table] = row_meta
        except sqlite3.Error:
            return metadata
        return metadata

    def _session_store_row(
        self,
        conn: sqlite3.Connection,
        table: str,
        session_id: str,
    ) -> dict[str, Any]:
        try:
            columns = [
                row[1] for row in conn.execute(f"pragma table_info({self._quote_ident(table)})")
            ]
        except sqlite3.Error:
            return {}

        session_column = next(
            (
                column
                for column in columns
                if column in {"session_id", "sessionId", "id", "session"}
            ),
            None,
        )
        if session_column is None:
            return {}

        try:
            row = conn.execute(
                f"select * from {self._quote_ident(table)} "
                f"where {self._quote_ident(session_column)} = ? limit 1",
                (session_id,),
            ).fetchone()
        except sqlite3.Error:
            return {}
        if row is None:
            return {}

        normalized: dict[str, Any] = {}
        for index, column in enumerate(columns):
            value = row[index]
            if isinstance(value, (str, int, float)) and value != "":
                normalized[column] = value
        return normalized

    def _record_role(self, record: dict[str, Any]) -> str | None:
        role = self._as_str(record.get("role"))
        if role in {"user", "assistant"}:
            return role

        message = record.get("message")
        if isinstance(message, dict):
            message_role = self._as_str(message.get("role"))
            if message_role in {"user", "assistant"}:
                return message_role

        data = record.get("data")
        if isinstance(data, dict):
            data_role = self._record_role(data)
            if data_role in {"user", "assistant"}:
                return data_role

        kind = (
            self._as_str(record.get("type"))
            or self._as_str(record.get("event"))
            or self._as_str(record.get("kind"))
            or ""
        ).lower()
        if any(token in kind for token in {"prompt", "user", "input"}):
            return "user"
        if any(token in kind for token in {"assistant", "response", "output", "completion"}):
            return "assistant"
        return None

    def _record_text(self, record: dict[str, Any]) -> str:
        for candidate in (
            record.get("content"),
            record.get("text"),
            record.get("message"),
            record.get("prompt"),
            record.get("response"),
            record.get("delta"),
            record.get("output"),
        ):
            text = self._content_to_text(candidate)
            if text:
                return text

        payload = record.get("payload")
        if isinstance(payload, dict):
            return self._record_text(payload)
        data = record.get("data")
        if isinstance(data, dict):
            return self._record_text(data)
        return ""

    def _record_timestamp(self, record: dict[str, Any]) -> datetime | None:
        for key in ("timestamp", "createdAt", "updatedAt", "time"):
            parsed = self._parse_ts(record.get(key))
            if parsed is not None:
                return parsed
        payload = record.get("payload")
        if isinstance(payload, dict):
            return self._record_timestamp(payload)
        data = record.get("data")
        if isinstance(data, dict):
            return self._record_timestamp(data)
        return None

    def _looks_like_tool_event(self, record: dict[str, Any]) -> bool:
        kind = (
            self._as_str(record.get("type"))
            or self._as_str(record.get("event"))
            or self._as_str(record.get("kind"))
            or ""
        ).lower()
        if "tool" in kind:
            return True
        payload = record.get("payload")
        if isinstance(payload, dict) and self._looks_like_tool_event(payload):
            return True
        data = record.get("data")
        return isinstance(data, dict) and self._looks_like_tool_event(data)

    def _tool_block_from_record(
        self,
        record: dict[str, Any],
        line_index: int,
        session_id: str,
    ) -> dict[str, Any] | None:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        if not isinstance(payload, dict):
            return None
        kind = self._as_str(payload.get("type")) or self._as_str(payload.get("kind")) or "tool"
        block_type = "tool_result" if "result" in kind.lower() else "tool_use"
        if block_type == "tool_result":
            return self._compact_dict(
                {
                    "block_type": "tool_result",
                    "tool_use_id": self._as_str(payload.get("toolCallId")),
                    "content": self._record_text(payload),
                    "is_error": bool(payload.get("isError")),
                }
            )
        return self._compact_dict(
            {
                "block_type": "tool_use",
                "tool_name": self._as_str(payload.get("toolName"))
                or self._as_str(payload.get("name"))
                or kind,
                "tool_id": self._as_str(payload.get("toolCallId"))
                or f"{session_id}:tool:{line_index}",
                "input": self._first_mapping(payload.get("input"), payload.get("arguments")),
            }
        )

    def _request_message_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            text = self._as_str(value.get("text"))
            if text:
                return text
            parts = value.get("parts")
            if isinstance(parts, list):
                collected = [self._content_to_text(part) for part in parts]
                return "\n\n".join(part for part in collected if part).strip()
        return self._content_to_text(value)

    def _response_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if not isinstance(value, list):
            return self._content_to_text(value)

        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            kind = self._as_str(item.get("kind")) or self._as_str(item.get("type")) or ""
            if kind in {"toolInvocation", "toolInvocationSerialized", "prepareToolInvocation"}:
                continue
            if kind in {"textEditGroup", "notebookEditGroup"}:
                parts.append("Made changes.")
                continue
            content = item.get("content")
            if isinstance(content, dict):
                content_value = self._as_str(content.get("value"))
                if content_value:
                    parts.append(content_value)
                    continue
            part_text = self._content_to_text(item)
            if part_text:
                parts.append(part_text)
        return "\n\n".join(part for part in parts if part).strip()

    def _response_tool_calls(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        response = request.get("response")
        if not isinstance(response, list):
            return []

        tool_calls: list[dict[str, Any]] = []
        request_id = self._as_str(request.get("requestId")) or "request"
        for index, item in enumerate(response):
            if not isinstance(item, dict):
                continue
            kind = self._as_str(item.get("kind")) or self._as_str(item.get("type")) or ""
            if kind not in {"toolInvocation", "toolInvocationSerialized", "prepareToolInvocation"}:
                continue
            tool_calls.append(
                self._compact_dict(
                    {
                        "block_type": "tool_use",
                        "tool_name": self._as_str(item.get("toolName"))
                        or self._as_str(item.get("name"))
                        or kind,
                        "tool_id": self._as_str(item.get("toolCallId"))
                        or f"{request_id}:tool:{index}",
                        "input": self._first_mapping(
                            item.get("input"),
                            item.get("arguments"),
                            item.get("toolInvocation"),
                        ),
                    }
                )
            )
        return tool_calls

    def _content_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("text", "value", "content", "message", "prompt", "response", "output"):
                text = self._content_to_text(value.get(key))
                if text:
                    return text
            return ""
        if isinstance(value, list):
            parts = [self._content_to_text(item) for item in value]
            return "\n\n".join(part for part in parts if part).strip()
        return ""

    def _iter_jsonl(self, path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_index, line in enumerate(handle, start=1):
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        yield line_index, record
        except OSError:
            return

    def _load_json(self, path: Path) -> Any:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _path_timestamp(self, path: Path) -> datetime:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            return datetime.fromtimestamp(0, tz=UTC)

    def _parse_ts(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e15:
                ts /= 1e6
            elif ts > 1e12:
                ts /= 1e3
            return datetime.fromtimestamp(ts, tz=UTC)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _format_ts(self, value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if item is not None}

    @staticmethod
    def _first_mapping(*values: Any) -> dict[str, Any] | None:
        for value in values:
            if isinstance(value, dict):
                return dict(value)
        return None

    @staticmethod
    def _quote_ident(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'
