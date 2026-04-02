from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.observe.catalog import discovered_roots, get_source


def _default_source_roots() -> tuple[Path, ...]:
    spec = get_source("codex")
    if spec is None:
        return ()
    return tuple(discovered_roots(spec))


_SESSION_DIR_NAMES = {"sessions", "archived_sessions"}
_MESSAGE_TYPES = {"message", "reasoning"}
_TOOL_CALL_TYPES = {"function_call", "custom_tool_call", "web_search_call", "computer_call"}
_TOOL_RESULT_TYPES = {
    "function_call_output",
    "custom_tool_call_output",
    "web_search_call_output",
    "computer_call_output",
}


class CodexObserveAdapter(ObserveAdapter):
    source = "codex"

    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        source_roots: Iterable[Path | str] | None = None,
    ):
        super().__init__(db, user_id)
        roots = source_roots or _default_source_roots()
        self.source_roots = tuple(Path(root).expanduser() for root in roots)
        self._session_index: dict[str, dict[str, Any]] | None = None

    def discover(self) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()

        for root in self.source_roots:
            for path in self._expand_candidates(root):
                if path in seen:
                    continue
                seen.add(path)
                discovered.append(path)

        return sorted(discovered, key=lambda path: str(path))

    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]:
        explicit_paths = self._normalize_candidate_paths(paths)
        candidates = explicit_paths if explicit_paths is not None else self.discover()

        for path in sorted(candidates, key=lambda candidate: str(candidate)):
            if explicit_paths is None and since:
                try:
                    if path.stat().st_mtime < since:
                        continue
                except OSError:
                    continue

            session = self._parse_session_file(path)
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
        return sorted(normalized, key=lambda path: str(path))

    def _expand_candidates(self, candidate: Path) -> list[Path]:
        try:
            resolved = candidate.resolve()
        except OSError:
            return []

        if resolved.is_file():
            if self._is_session_file(resolved):
                return [resolved]
            return []

        if not resolved.is_dir():
            return []

        results: list[Path] = []
        for child in resolved.rglob("*.jsonl"):
            try:
                child_resolved = child.resolve()
            except OSError:
                continue
            if self._is_session_file(child_resolved):
                results.append(child_resolved)
        return results

    def _is_session_file(self, path: Path) -> bool:
        if path.suffix != ".jsonl" or not path.is_file():
            return False
        if path.name == "session_index.jsonl":
            return False
        return any(part in _SESSION_DIR_NAMES for part in path.parts)

    def _parse_session_file(self, path: Path) -> ObservedSession | None:
        turns: list[ObservedTurn] = []
        current_assistant_turn: ObservedTurn | None = None
        pending_tool_turn: ObservedTurn | None = None
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        session_meta: dict[str, Any] | None = None
        source_line_count = 0
        sequence_fallback = 0
        start_time: datetime | None = None
        end_time: datetime | None = None
        last_timestamp: datetime | None = None
        session_id = self._session_id_from_path(path)

        for line_index, record in self._iter_jsonl(path):
            source_line_count = line_index
            wrapped = self._unwrap_record(record)
            if wrapped is None:
                continue

            wrapper_type, payload = wrapped
            record_timestamp = self._parse_ts(record.get("timestamp"))
            payload_timestamp = (
                self._parse_ts(payload.get("timestamp")) if isinstance(payload, dict) else None
            )
            timestamp = record_timestamp or payload_timestamp or last_timestamp
            if timestamp is None:
                timestamp = self._fallback_timestamp(start_time, sequence_fallback)
                sequence_fallback += 1

            last_timestamp = timestamp
            start_time = timestamp if start_time is None or timestamp < start_time else start_time
            end_time = timestamp if end_time is None or timestamp > end_time else end_time

            if wrapper_type == "session_meta":
                if session_meta is None:
                    session_meta = payload
                payload_session_id = self._as_str(payload.get("id"))
                if payload_session_id == self._session_id_from_path(path):
                    session_meta = payload
                    session_id = payload_session_id
                elif payload_session_id and session_meta is None:
                    session_id = payload_session_id
                continue

            item_type = self._as_str(payload.get("type")) or wrapper_type

            if item_type in _MESSAGE_TYPES:
                role = self._as_str(payload.get("role"))
                content = self._extract_text_content(payload)
                if not role or not content:
                    continue
                if role == "user":
                    turns.append(
                        ObservedTurn(
                            role="user",
                            content=content,
                            timestamp=timestamp,
                            metadata={
                                "source_event_type": item_type,
                                "source_wrapper_type": wrapper_type,
                                "source_line_index": line_index,
                            },
                        )
                    )
                    current_assistant_turn = None
                    pending_tool_turn = None
                    continue
                if role != "assistant":
                    continue
                current_assistant_turn = self._ensure_assistant_turn(
                    turns,
                    current_assistant_turn,
                    timestamp,
                    line_index,
                    wrapper_type,
                    item_type,
                )
                pending_tool_turn = current_assistant_turn
                self._append_assistant_content(current_assistant_turn, content, item_type)
                continue

            if item_type in _TOOL_CALL_TYPES:
                current_assistant_turn = self._ensure_assistant_turn(
                    turns,
                    current_assistant_turn,
                    timestamp,
                    line_index,
                    wrapper_type,
                    item_type,
                )
                pending_tool_turn = current_assistant_turn
                tool_block = self._tool_call_block(session_id, line_index, item_type, payload)
                tool_id = self._as_str(tool_block.get("tool_id"))
                if tool_id:
                    pending_tool_calls[tool_id] = tool_block
                current_assistant_turn.tool_calls.append(tool_block)
                continue

            if item_type in _TOOL_RESULT_TYPES:
                tool_result_turn = pending_tool_turn or current_assistant_turn
                if tool_result_turn is None:
                    tool_result_turn = self._ensure_assistant_turn(
                        turns,
                        current_assistant_turn,
                        timestamp,
                        line_index,
                        wrapper_type,
                        item_type,
                    )
                    current_assistant_turn = tool_result_turn
                    pending_tool_turn = tool_result_turn
                tool_result_turn.tool_calls.append(
                    self._tool_result_block(item_type, payload, pending_tool_calls)
                )
                continue

        if not turns or start_time is None:
            return None

        session_info = session_meta or {}
        session_id = self._as_str(session_info.get("id")) or session_id
        parent_session_id = self._extract_parent_session_id(session_info)
        is_subagent = parent_session_id is not None or self._has_subagent_source(session_info)
        project = self._as_str(session_info.get("cwd"))
        agent_nickname = self._as_str(session_info.get("agent_nickname"))
        agent_role = self._as_str(session_info.get("agent_role"))

        metadata: dict[str, Any] = {
            "artifact_family": "session_jsonl",
            "cli_version": self._as_str(session_info.get("cli_version")),
            "originator": self._as_str(session_info.get("originator")),
            "model_provider": self._as_str(session_info.get("model_provider")),
            "source_root": str(path.parent),
            "source_line_count": source_line_count,
        }
        if session_info.get("source") is not None:
            metadata["source"] = session_info.get("source")
        if session_info.get("base_instructions") is not None:
            metadata["base_instructions"] = session_info.get("base_instructions")
        if session_info.get("git") is not None:
            metadata["git"] = session_info.get("git")
        if session_info.get("forked_from_id") is not None:
            metadata["forked_from_id"] = session_info.get("forked_from_id")

        index_entry = self._load_session_index().get(session_id)
        if index_entry:
            thread_name = self._as_str(index_entry.get("thread_name"))
            updated_at = self._as_str(index_entry.get("updated_at"))
            if thread_name:
                metadata["thread_name"] = thread_name
            if updated_at:
                metadata["indexed_updated_at"] = updated_at

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            project=project,
            parent_session_id=parent_session_id,
            turns=turns,
            metadata={k: v for k, v in metadata.items() if v is not None},
            is_subagent=is_subagent,
            agent_id=agent_nickname,
            agent_slug=agent_role,
            source_instance_id=str(path),
        )

    def _unwrap_record(self, record: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        wrapper_type = self._as_str(record.get("type"))
        payload = record.get("payload")
        if wrapper_type and isinstance(payload, dict):
            return wrapper_type, payload
        if wrapper_type and isinstance(record, dict):
            return wrapper_type, record
        if record.get("record_type") == "state":
            return None
        if self._as_str(record.get("id")) and record.get("timestamp") is not None:
            return "session_meta", record
        return None

    def _ensure_assistant_turn(
        self,
        turns: list[ObservedTurn],
        current: ObservedTurn | None,
        timestamp: datetime,
        line_index: int,
        wrapper_type: str,
        item_type: str,
    ) -> ObservedTurn:
        if current is not None:
            if timestamp < current.timestamp:
                current.timestamp = timestamp
            return current
        turn = ObservedTurn(
            role="assistant",
            content="",
            timestamp=timestamp,
            metadata={
                "source_event_type": item_type,
                "source_wrapper_type": wrapper_type,
                "source_line_index": line_index,
            },
        )
        turns.append(turn)
        return turn

    def _append_assistant_content(self, turn: ObservedTurn, content: str, item_type: str) -> None:
        if not content:
            return
        if turn.content:
            turn.content += "\n\n"
        if item_type == "reasoning":
            turn.content += f"[reasoning]\n{content}"
        else:
            turn.content += content

    def _tool_call_block(
        self,
        session_id: str,
        line_index: int,
        item_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        tool_name = self._as_str(payload.get("name")) or item_type
        tool_id = self._as_str(payload.get("call_id")) or f"{session_id}:tool:{line_index}"
        arguments = payload.get("arguments")
        tool_input = self._coerce_tool_input(payload.get("input"))
        if tool_input is None:
            tool_input = self._coerce_tool_input(arguments)
        block: dict[str, Any] = {
            "block_type": "tool_use",
            "tool_name": tool_name,
            "tool_id": tool_id,
            "input": tool_input,
        }
        status = self._as_str(payload.get("status"))
        if status:
            block["status"] = status
        if item_type != "function_call":
            block["tool_kind"] = item_type
        return block

    def _tool_result_block(
        self,
        item_type: str,
        payload: dict[str, Any],
        pending_tool_calls: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        call_id = self._as_str(payload.get("call_id"))
        matched_call = pending_tool_calls.get(call_id or "") if call_id else None
        output = payload.get("output")
        is_error = False
        if isinstance(output, str):
            parsed_output = self._maybe_parse_json(output)
            if isinstance(parsed_output, dict):
                metadata = parsed_output.get("metadata")
                if isinstance(metadata, dict):
                    exit_code = metadata.get("exit_code")
                    if isinstance(exit_code, int) and exit_code != 0:
                        is_error = True
            output_content = output
        else:
            output_content = self._stringify(output)

        status = self._as_str(payload.get("status"))
        if status and status not in {"completed", "succeeded", "success"}:
            is_error = True

        block: dict[str, Any] = {
            "block_type": "tool_result",
            "tool_use_id": call_id,
            "content": output_content,
            "is_error": is_error,
        }
        if matched_call is not None:
            block["tool_name"] = matched_call.get("tool_name")
        if status:
            block["status"] = status
        if item_type != "function_call_output":
            block["tool_kind"] = item_type
        return block

    def _extract_text_content(self, payload: dict[str, Any]) -> str:
        direct_text = self._as_str(payload.get("text"))
        if direct_text:
            return direct_text

        content = payload.get("content")
        if not isinstance(content, list):
            return ""

        pieces: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = self._as_str(block.get("type"))
            if block_type in {"input_text", "output_text", "summary_text", "text"}:
                text = self._as_str(block.get("text"))
                if text:
                    pieces.append(text)
                continue
            if block_type in {"input_image", "output_image", "image"}:
                pieces.append(f"[{block_type}]")
                continue
            if block_type:
                raw_text = self._as_str(block.get("text"))
                if raw_text:
                    pieces.append(raw_text)
        return "\n\n".join(piece for piece in pieces if piece)

    def _extract_parent_session_id(self, session_meta: dict[str, Any]) -> str | None:
        forked_from_id = self._as_str(session_meta.get("forked_from_id"))
        if forked_from_id:
            return forked_from_id

        source = session_meta.get("source")
        if isinstance(source, dict):
            subagent = source.get("subagent")
            if isinstance(subagent, dict):
                thread_spawn = subagent.get("thread_spawn")
                if isinstance(thread_spawn, dict):
                    parent_thread_id = self._as_str(thread_spawn.get("parent_thread_id"))
                    if parent_thread_id:
                        return parent_thread_id
        return None

    def _has_subagent_source(self, session_meta: dict[str, Any]) -> bool:
        source = session_meta.get("source")
        return isinstance(source, dict) and isinstance(source.get("subagent"), dict)

    def _session_id_from_path(self, path: Path) -> str:
        name = path.stem
        match = re.search(r"([0-9a-f]{8,}(?:-[0-9a-f]{4,}){2,})$", name)
        if match:
            return match.group(1)
        return name

    def _fallback_timestamp(self, start_time: datetime | None, sequence: int) -> datetime:
        base = start_time or datetime.fromtimestamp(0, tz=UTC)
        return base + timedelta(microseconds=sequence)

    def _iter_jsonl(self, path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_index, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        yield line_index, record
        except OSError:
            return

    def _load_session_index(self) -> dict[str, dict[str, Any]]:
        if self._session_index is not None:
            return self._session_index

        index: dict[str, dict[str, Any]] = {}
        for root in self.source_roots:
            index_path = root.expanduser() / "session_index.jsonl"
            try:
                resolved = index_path.resolve()
            except OSError:
                continue
            if not resolved.is_file():
                continue
            for _, record in self._iter_jsonl(resolved):
                session_id = self._as_str(record.get("id"))
                if session_id:
                    index[session_id] = record
        self._session_index = index
        return index

    def _parse_ts(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=UTC)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def _coerce_tool_input(self, value: Any) -> Any:
        if isinstance(value, str):
            parsed = self._maybe_parse_json(value)
            return parsed
        return value

    def _maybe_parse_json(self, value: str) -> Any:
        try:
            return json.loads(value)
        except Exception:
            return value

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    def _as_str(self, value: Any) -> str | None:
        return value if isinstance(value, str) and value else None
