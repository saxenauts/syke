from __future__ import annotations

import json
import os
import re
import sqlite3
import tomllib
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
_SESSION_INDEX_FILENAME = "session_index.jsonl"
_STATE_DB_FILENAME_RE = re.compile(r"^state(?:_\d+)?\.sqlite$")
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
        self._configured_source_roots = (
            tuple(Path(root).expanduser() for root in source_roots)
            if source_roots is not None
            else None
        )
        self._session_index: dict[str, dict[str, Any]] | None = None
        self._state_threads: dict[str, dict[str, Any]] | None = None
        self._state_threads_by_rollout: dict[str, dict[str, Any]] | None = None

    def _source_roots(self) -> tuple[Path, ...]:
        return self._configured_source_roots or _default_source_roots()

    def discover(self) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()

        for root in self._source_roots():
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
            if self._is_session_index_file(resolved):
                return self._session_files_for_index(resolved)
            if self._is_state_db_file(resolved):
                return self._session_files_for_state_db(resolved)
            return []

        if not resolved.is_dir():
            return []

        return self._rollout_files_under(resolved)

    def _is_session_file(self, path: Path) -> bool:
        if path.suffix != ".jsonl" or not path.is_file():
            return False
        if self._is_session_index_file(path):
            return False
        return any(part in _SESSION_DIR_NAMES for part in path.parts)

    def _is_session_index_file(self, path: Path) -> bool:
        return path.name == _SESSION_INDEX_FILENAME and path.is_file()

    def _is_state_db_file(self, path: Path) -> bool:
        return path.is_file() and bool(_STATE_DB_FILENAME_RE.fullmatch(path.name))

    def _rollout_files_under(self, root: Path) -> list[Path]:
        results: list[Path] = []
        for dir_name in sorted(_SESSION_DIR_NAMES):
            session_root = root / dir_name
            if not session_root.is_dir():
                continue
            for child in session_root.rglob("*.jsonl"):
                try:
                    child_resolved = child.resolve()
                except OSError:
                    continue
                if self._is_session_file(child_resolved):
                    results.append(child_resolved)
        return sorted(results, key=lambda path: str(path))

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
        state_entry = self._load_state_threads().get(session_id)
        if state_entry is None:
            state_entry = self._state_threads_by_rollout_path().get(str(path))
        if state_entry:
            project = project or self._as_str(state_entry.get("cwd"))
            agent_nickname = agent_nickname or self._as_str(state_entry.get("agent_nickname"))
            agent_role = agent_role or self._as_str(state_entry.get("agent_role"))

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

        if state_entry:
            metadata["state_db_path"] = self._as_str(state_entry.get("state_db_path"))
            metadata["title"] = self._as_str(state_entry.get("title"))
            metadata["session_source"] = self._as_str(state_entry.get("source"))
            metadata["agent_path"] = self._as_str(state_entry.get("agent_path"))
            metadata["model"] = self._as_str(state_entry.get("model"))
            metadata["reasoning_effort"] = self._as_str(state_entry.get("reasoning_effort"))
            metadata["sandbox_policy"] = self._as_str(state_entry.get("sandbox_policy"))
            metadata["approval_mode"] = self._as_str(state_entry.get("approval_mode"))
            metadata["first_user_message"] = self._as_str(state_entry.get("first_user_message"))
            metadata["created_at"] = self._as_str(state_entry.get("created_at"))
            metadata["updated_at"] = self._as_str(state_entry.get("updated_at"))
            metadata["archived_at"] = self._as_str(state_entry.get("archived_at"))
            metadata["state_rollout_path"] = self._as_str(state_entry.get("rollout_path"))
            metadata["git_sha"] = self._as_str(state_entry.get("git_sha"))
            metadata["git_branch"] = self._as_str(state_entry.get("git_branch"))
            metadata["git_origin_url"] = self._as_str(state_entry.get("git_origin_url"))
            if metadata.get("model_provider") is None:
                metadata["model_provider"] = self._as_str(state_entry.get("model_provider"))
            tokens_used = state_entry.get("tokens_used")
            if isinstance(tokens_used, int):
                metadata["tokens_used"] = tokens_used

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
        for root in self._source_roots():
            index_path = (
                root.expanduser()
                if self._is_session_index_file(root.expanduser())
                else root.expanduser() / _SESSION_INDEX_FILENAME
            )
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

    def _session_files_for_index(self, index_path: Path) -> list[Path]:
        session_ids: set[str] = set()
        for _, record in self._iter_jsonl(index_path):
            session_id = self._as_str(record.get("id"))
            if session_id:
                session_ids.add(session_id)
        return self._session_files_for_ids(session_ids, index_root=index_path.parent)

    def _session_files_for_state_db(self, db_path: Path) -> list[Path]:
        session_ids: set[str] = set()
        paths: list[Path] = []
        seen: set[Path] = set()

        for entry in self._iter_state_thread_entries(db_path):
            session_id = self._as_str(entry.get("id"))
            if session_id:
                session_ids.add(session_id)
            rollout_path = self._resolve_rollout_path(entry.get("rollout_path"))
            if (
                rollout_path is None
                or rollout_path in seen
                or not self._is_session_file(rollout_path)
            ):
                continue
            seen.add(rollout_path)
            paths.append(rollout_path)

        for path in self._session_files_for_ids(session_ids):
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)

        return sorted(paths, key=lambda path: str(path))

    def _session_files_for_ids(
        self,
        session_ids: set[str],
        *,
        index_root: Path | None = None,
    ) -> list[Path]:
        if not session_ids:
            return []

        results: list[Path] = []
        seen: set[Path] = set()
        by_id = self._rollout_paths_by_session_id()

        for session_id in sorted(session_ids):
            state_entry = self._load_state_threads().get(session_id)
            if state_entry is not None:
                rollout_path = self._resolve_rollout_path(state_entry.get("rollout_path"))
                if rollout_path is not None and self._is_session_file(rollout_path):
                    if rollout_path not in seen:
                        seen.add(rollout_path)
                        results.append(rollout_path)
                    continue

            rollout_path = by_id.get(session_id)
            if rollout_path is not None and rollout_path not in seen:
                seen.add(rollout_path)
                results.append(rollout_path)

        if results or index_root is None:
            return sorted(results, key=lambda path: str(path))

        index_parent = index_root.resolve()
        if index_parent.name == "archived_sessions":
            base_root = index_parent.parent
        else:
            base_root = index_parent
        for path in self._rollout_files_under(base_root):
            if path in seen:
                continue
            seen.add(path)
            results.append(path)
        return sorted(results, key=lambda path: str(path))

    def _rollout_paths_by_session_id(self) -> dict[str, Path]:
        paths_by_id: dict[str, Path] = {}
        for path in self.discover():
            session_id = self._session_id_from_path(path)
            existing = paths_by_id.get(session_id)
            if existing is None or str(path) > str(existing):
                paths_by_id[session_id] = path
        return paths_by_id

    def _load_state_threads(self) -> dict[str, dict[str, Any]]:
        if self._state_threads is not None and self._state_threads_by_rollout is not None:
            return self._state_threads

        threads: dict[str, dict[str, Any]] = {}
        threads_by_rollout: dict[str, dict[str, Any]] = {}
        for db_path in self._state_db_paths():
            for entry in self._iter_state_thread_entries(db_path):
                session_id = self._as_str(entry.get("id"))
                if session_id:
                    current = threads.get(session_id)
                    if current is None or self._state_entry_sort_key(
                        entry
                    ) >= self._state_entry_sort_key(current):
                        threads[session_id] = entry

                rollout_path = self._resolve_rollout_path(entry.get("rollout_path"))
                if rollout_path is None:
                    continue
                rollout_key = str(rollout_path)
                current_by_path = threads_by_rollout.get(rollout_key)
                if current_by_path is None or self._state_entry_sort_key(
                    entry
                ) >= self._state_entry_sort_key(current_by_path):
                    threads_by_rollout[rollout_key] = entry

        self._state_threads = threads
        self._state_threads_by_rollout = threads_by_rollout
        return threads

    def _state_threads_by_rollout_path(self) -> dict[str, dict[str, Any]]:
        self._load_state_threads()
        return self._state_threads_by_rollout or {}

    def _state_db_paths(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for root in self._source_roots():
            candidates: list[Path] = []
            expanded = root.expanduser()
            if self._is_state_db_file(expanded):
                candidates.append(expanded)
            elif expanded.is_dir():
                candidates.extend(self._sqlite_homes_for_root(expanded))
            for candidate in candidates:
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved.is_dir():
                    for child in sorted(resolved.iterdir(), key=lambda path: path.name):
                        if not self._is_state_db_file(child):
                            continue
                        try:
                            child_resolved = child.resolve()
                        except OSError:
                            continue
                        if child_resolved in seen:
                            continue
                        seen.add(child_resolved)
                        paths.append(child_resolved)
                    continue
                if resolved.is_file() and resolved not in seen:
                    seen.add(resolved)
                    paths.append(resolved)
        return sorted(paths, key=lambda path: str(path))

    def _sqlite_homes_for_root(self, root: Path) -> list[Path]:
        candidates: list[Path] = []
        raw_env = os.getenv("CODEX_SQLITE_HOME")
        if raw_env:
            candidates.append(self._resolve_config_path(root, raw_env))

        config_path = root / "config.toml"
        try:
            if config_path.is_file():
                with config_path.open("rb") as handle:
                    config = tomllib.load(handle)
                sqlite_home = config.get("sqlite_home")
                if isinstance(sqlite_home, str) and sqlite_home:
                    candidates.append(self._resolve_config_path(root, sqlite_home))
        except (OSError, tomllib.TOMLDecodeError):
            pass

        candidates.append(root / "sqlite")
        candidates.append(root)

        unique: list[Path] = []
        seen: set[Path] = set()
        for candidate in candidates:
            expanded = candidate.expanduser()
            try:
                resolved = expanded.resolve()
            except OSError:
                resolved = expanded
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(resolved)
        return unique

    def _resolve_config_path(self, root: Path, value: str) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return (root / path).expanduser()

    def _iter_state_thread_entries(self, db_path: Path) -> Iterable[dict[str, Any]]:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return

        try:
            conn.row_factory = sqlite3.Row
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(threads)")
                if isinstance(row, sqlite3.Row) and row["name"]
            }
            if not columns:
                return

            wanted = [
                "id",
                "rollout_path",
                "created_at",
                "updated_at",
                "source",
                "agent_nickname",
                "agent_role",
                "agent_path",
                "model_provider",
                "model",
                "reasoning_effort",
                "cwd",
                "cli_version",
                "title",
                "sandbox_policy",
                "approval_mode",
                "tokens_used",
                "first_user_message",
                "archived_at",
                "git_sha",
                "git_branch",
                "git_origin_url",
            ]
            select_columns = [column for column in wanted if column in columns]
            if not {"id", "rollout_path"} <= set(select_columns):
                return

            query = f"SELECT {', '.join(select_columns)} FROM threads"
            for row in conn.execute(query):
                if not isinstance(row, sqlite3.Row):
                    continue
                entry = dict(row)
                entry["state_db_path"] = str(db_path)
                created_at = self._parse_ts(entry.get("created_at"))
                updated_at = self._parse_ts(entry.get("updated_at"))
                archived_at = self._parse_ts(entry.get("archived_at"))
                if created_at is not None:
                    entry["created_at"] = created_at.isoformat()
                if updated_at is not None:
                    entry["updated_at"] = updated_at.isoformat()
                if archived_at is not None:
                    entry["archived_at"] = archived_at.isoformat()
                yield entry
        except sqlite3.Error:
            return
        finally:
            conn.close()

    def _resolve_rollout_path(self, raw_path: Any) -> Path | None:
        rollout_path = self._as_str(raw_path)
        if not rollout_path:
            return None
        candidate = Path(rollout_path).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        return resolved if resolved.is_file() else None

    def _state_entry_sort_key(self, entry: dict[str, Any]) -> tuple[float, str, str]:
        updated_at = self._parse_ts(entry.get("updated_at"))
        created_at = self._parse_ts(entry.get("created_at"))
        timestamp = updated_at or created_at or datetime.fromtimestamp(0, tz=UTC)
        state_db_path = self._as_str(entry.get("state_db_path")) or ""
        rollout_path = self._as_str(entry.get("rollout_path")) or ""
        return (timestamp.timestamp(), state_db_path, rollout_path)

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
