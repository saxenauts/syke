from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Protocol, TypeVar, cast, override

from syke.config_file import expand_path
from syke.db import SykeDB
from syke.ingestion.constants import ROLE_ASSISTANT, ROLE_USER
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn
from syke.ingestion.parsers import parse_timestamp, read_jsonl
from syke.models import Event

logger = logging.getLogger(__name__)


class _DiscoverRoot(Protocol):
    path: str
    include: list[str]


class _DiscoverConfig(Protocol):
    roots: list[_DiscoverRoot]


class _CodexDescriptor(Protocol):
    discover: _DiscoverConfig | None

    def expand_external_id(self, **values: object) -> str: ...


AdapterT = TypeVar("AdapterT", bound=ObserveAdapter)


class _RegisterAdapter(Protocol):
    def __call__(self, source: str) -> Callable[[type[AdapterT]], type[AdapterT]]: ...


_descriptor_module: ModuleType = importlib.import_module("syke.ingestion.descriptor")
load_descriptor = cast(
    Callable[[Path], _CodexDescriptor],
    _descriptor_module.load_descriptor,
)
register_adapter = cast(
    _RegisterAdapter,
    importlib.import_module("syke.sense.registry").register_adapter,
)


@register_adapter("codex")
class CodexAdapter(ObserveAdapter):
    source: str = "codex"

    _TURN_TYPES = {"message", "reasoning", "function_call", "function_call_output"}
    _TOOL_USE_TYPES = {"function_call", "custom_tool_call", "web_search_call"}
    _TOOL_RESULT_TYPES = {"function_call_output", "custom_tool_call_output"}

    def __init__(self, db: SykeDB, user_id: str):
        super().__init__(db, user_id)
        self._descriptor: _CodexDescriptor = load_descriptor(
            Path(__file__).parent / "descriptors" / "codex.toml"
        )

    @override
    def discover(self) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()
        last_sync_epoch = self._last_sync_epoch()

        roots: list[tuple[str, list[str]]] = []
        if self._descriptor.discover:
            for root in self._descriptor.discover.roots:
                roots.append((root.path, root.include))
        if not roots:
            roots.append(("~/.codex/sessions", ["**/rollout-*.jsonl"]))

        for root_path_str, root_includes in roots:
            root_path = expand_path(root_path_str)
            if not root_path.exists():
                continue

            includes = root_includes or ["**/rollout-*.jsonl"]
            for pattern in includes:
                for fpath in root_path.glob(pattern):
                    if not fpath.is_file() or fpath in seen:
                        continue
                    seen.add(fpath)
                    if fpath.stat().st_mtime < last_sync_epoch:
                        continue
                    discovered.append(fpath)

        discovered.sort(key=os.path.getmtime)
        return discovered

    @override
    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        cutoff = since if since > 0 else self._last_sync_epoch()
        covered_session_ids: set[str] = set()

        for fpath in self.discover():
            if cutoff and fpath.stat().st_mtime < cutoff:
                continue

            session_id = self._session_id_from_path(fpath) or fpath.stem
            covered_session_ids.add(session_id)

            try:
                session = self._parse_session_file(fpath, session_id)
                if session is not None:
                    yield session
            except Exception as exc:
                logger.warning("Failed to parse Codex session %s: %s", fpath.name, exc)

        history_path = expand_path("~/.codex/history.jsonl")
        if not history_path.exists():
            return
        if cutoff and history_path.stat().st_mtime < cutoff:
            return

        try:
            for session in self._iter_history_sessions(history_path, covered_session_ids):
                yield session
        except Exception as exc:
            logger.warning("Failed to parse Codex history fallback: %s", exc)

    def _parse_session_file(self, fpath: Path, session_id: str) -> ObservedSession | None:
        lines = read_jsonl(fpath)
        if not lines:
            return None

        start_time = self._first_valid_timestamp(lines) or self._parse_timestamp_from_path(fpath)
        if start_time is None:
            return None

        end_time = self._last_valid_timestamp(lines) or start_time
        session_meta = self._first_session_meta(lines)

        turns: list[ObservedTurn] = []
        pending_reasoning: list[str] = []
        last_assistant_turn: ObservedTurn | None = None
        harness_line_types: dict[str, int] = {}

        for idx, line in enumerate(lines):
            line_type = line.get("type")

            # Normalize old vs new Codex format into a common payload dict.
            # New format (Dec 2025+): type="response_item", payload={type, role, ...}
            # Old format (pre-Dec 2025): type="message"|"function_call"|etc directly
            if line_type == "response_item":
                payload_obj = line.get("payload")
                if not isinstance(payload_obj, dict):
                    continue
                payload = cast(dict[str, object], payload_obj)
            elif line_type in {
                "message",
                "reasoning",
                "function_call",
                "function_call_output",
                "custom_tool_call",
                "custom_tool_call_output",
                "web_search_call",
            }:
                payload = cast(dict[str, object], line)
            else:
                key = str(line_type) if line_type else "unknown"
                harness_line_types[key] = harness_line_types.get(key, 0) + 1
                continue

            payload_type_obj = payload.get("type")
            payload_type = payload_type_obj if isinstance(payload_type_obj, str) else ""
            if (
                payload_type
                and payload_type not in self._TURN_TYPES
                and payload_type
                not in {
                    *self._TOOL_USE_TYPES,
                    *self._TOOL_RESULT_TYPES,
                }
            ):
                continue

            timestamp = parse_timestamp(line) or start_time

            if payload_type == "message":
                role_obj = payload.get("role")
                role = role_obj if isinstance(role_obj, str) else ""

                if role == "developer":
                    continue
                if role not in {ROLE_USER, ROLE_ASSISTANT}:
                    continue

                content = self._extract_content(payload.get("content"))
                if role == ROLE_ASSISTANT and pending_reasoning:
                    content = self._prepend_reasoning(content, pending_reasoning)
                    pending_reasoning = []

                turn = ObservedTurn(
                    role=role,
                    content=content,
                    timestamp=timestamp,
                    metadata={
                        "source_line_index": idx,
                        "source_event_type": "response_item",
                    },
                )
                turns.append(turn)

                if role == ROLE_ASSISTANT:
                    last_assistant_turn = turn
                continue

            if payload_type == "reasoning":
                reasoning_text = self._extract_reasoning_text(payload)
                if reasoning_text:
                    pending_reasoning.append(reasoning_text)
                continue

            if payload_type in self._TOOL_USE_TYPES:
                if last_assistant_turn is None:
                    continue
                tool_call = self._tool_use_from_payload(payload_type, payload, idx)
                if tool_call is not None:
                    last_assistant_turn.tool_calls.append(tool_call)
                continue

            if payload_type in self._TOOL_RESULT_TYPES:
                if last_assistant_turn is None:
                    continue
                tool_result = self._tool_result_from_payload(payload_type, payload, idx)
                if tool_result is not None:
                    last_assistant_turn.tool_calls.append(tool_result)

        if pending_reasoning and last_assistant_turn is not None:
            last_assistant_turn.content = self._prepend_reasoning(
                last_assistant_turn.content,
                pending_reasoning,
            )

        metadata: dict[str, object] = {
            "store": "session",
            "total_lines": len(lines),
            "turn_count": len(turns),
            "user_turns": sum(1 for t in turns if t.role == ROLE_USER),
            "assistant_turns": sum(1 for t in turns if t.role == ROLE_ASSISTANT),
            "duration_minutes": round(max(0.0, (end_time - start_time).total_seconds() / 60.0), 1),
        }

        if harness_line_types:
            metadata["harness_line_types"] = harness_line_types

        cwd = self._extract_meta_field(session_meta, "cwd")
        git_branch = self._extract_meta_field(session_meta, "git.branch")
        model_provider = self._extract_meta_field(session_meta, "model_provider")

        if isinstance(cwd, str) and cwd:
            metadata["cwd"] = cwd
            metadata["project"] = self._normalize_project_path(cwd)
        if isinstance(git_branch, str) and git_branch:
            metadata["git_branch"] = git_branch
        if isinstance(model_provider, str) and model_provider:
            metadata["model_provider"] = model_provider

        root_path = fpath.parent
        relative_path = fpath.name
        source_instance_id = hashlib.sha256(
            f"{self.source}:{root_path}:{relative_path}".encode()
        ).hexdigest()[:12]

        return ObservedSession(
            session_id=session_id,
            source_path=fpath,
            start_time=start_time,
            end_time=end_time,
            project=cast(str | None, metadata.get("project")),
            turns=turns,
            metadata=metadata,
            source_instance_id=source_instance_id,
        )

    def _iter_history_sessions(
        self,
        history_path: Path,
        covered_session_ids: set[str],
    ) -> Iterable[ObservedSession]:
        lines = read_jsonl(history_path)
        if not lines:
            return

        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for line in lines:
            session_id_obj = line.get("session_id")
            if not isinstance(session_id_obj, str) or not session_id_obj:
                continue
            if session_id_obj in covered_session_ids:
                continue
            grouped[session_id_obj].append(line)

        for session_id, entries in grouped.items():
            session = self._parse_history_session(history_path, session_id, entries)
            if session is not None:
                yield session

    def _parse_history_session(
        self,
        history_path: Path,
        session_id: str,
        entries: list[dict[str, object]],
    ) -> ObservedSession | None:
        sorted_entries = sorted(entries, key=self._history_sort_key)

        turns: list[ObservedTurn] = []
        for idx, entry in enumerate(sorted_entries):
            text_obj = entry.get("text")
            if not isinstance(text_obj, str) or not text_obj:
                continue

            timestamp = self._parse_history_timestamp(entry.get("ts"))
            if timestamp is None:
                continue

            turns.append(
                ObservedTurn(
                    role=ROLE_USER,
                    content=text_obj,
                    timestamp=timestamp,
                    metadata={
                        "source_line_index": idx,
                        "source_event_type": "history",
                    },
                )
            )

        if not turns:
            return None

        start_time = turns[0].timestamp
        end_time = turns[-1].timestamp

        metadata: dict[str, object] = {
            "store": "history",
            "total_lines": len(sorted_entries),
            "turn_count": len(turns),
            "user_turns": len(turns),
            "assistant_turns": 0,
            "duration_minutes": round(max(0.0, (end_time - start_time).total_seconds() / 60.0), 1),
        }

        root_path = history_path.parent
        relative_path = history_path.name
        source_instance_id = hashlib.sha256(
            f"{self.source}:{root_path}:{relative_path}".encode()
        ).hexdigest()[:12]

        return ObservedSession(
            session_id=session_id,
            source_path=history_path,
            start_time=start_time,
            end_time=end_time,
            turns=turns,
            metadata=metadata,
            source_instance_id=source_instance_id,
        )

    @override
    def _make_turn_event(
        self,
        session: ObservedSession,
        turn: ObservedTurn,
        idx: int,
        seq_idx: int,
    ) -> Event:
        event = super()._make_turn_event(session, turn, idx, seq_idx)
        event.external_id = self._descriptor.expand_external_id(
            session_id=session.session_id,
            sequence_index=idx,
        )
        return event

    def _extract_content(self, raw_content: object) -> str:
        if isinstance(raw_content, str):
            return raw_content
        if not isinstance(raw_content, list):
            return ""

        parts: list[str] = []
        for block in raw_content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")
            if block_type in {"input_text", "output_text", "text"}:
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)

        return "\n".join(parts)

    def _extract_reasoning_text(self, payload: dict[str, object]) -> str:
        parts: list[str] = []

        text_obj = payload.get("text")
        if isinstance(text_obj, str) and text_obj:
            parts.append(text_obj)

        summary_obj = payload.get("summary")
        if isinstance(summary_obj, list):
            for summary_item in summary_obj:
                if isinstance(summary_item, str):
                    parts.append(summary_item)
                    continue
                if isinstance(summary_item, dict):
                    summary_text = summary_item.get("text")
                    if isinstance(summary_text, str) and summary_text:
                        parts.append(summary_text)

        content_text = self._extract_content(payload.get("content"))
        if content_text:
            parts.append(content_text)

        return "\n".join(part for part in parts if part)

    @staticmethod
    def _prepend_reasoning(content: str, reasoning_blocks: list[str]) -> str:
        prefix = "\n".join(f"[thinking]\n{block}" for block in reasoning_blocks if block)
        if not prefix:
            return content
        if not content:
            return prefix
        return f"{prefix}\n{content}"

    def _tool_use_from_payload(
        self,
        payload_type: str,
        payload: dict[str, object],
        source_line_index: int,
    ) -> dict[str, object] | None:
        tool_name_obj = payload.get("name") or payload.get("tool_name")
        tool_name = (
            tool_name_obj if isinstance(tool_name_obj, str) and tool_name_obj else payload_type
        )

        tool_id_obj = payload.get("call_id") or payload.get("id") or payload.get("tool_call_id")
        tool_id: str | None
        if isinstance(tool_id_obj, str) and tool_id_obj:
            tool_id = tool_id_obj
        else:
            tool_id = f"{payload_type}-{source_line_index}"

        input_payload: dict[str, object] = {}
        input_obj = payload.get("input")
        if isinstance(input_obj, dict):
            input_payload = cast(dict[str, object], input_obj)

        arguments_obj = payload.get("arguments")
        if isinstance(arguments_obj, dict):
            input_payload = cast(dict[str, object], arguments_obj)
        elif isinstance(arguments_obj, str) and arguments_obj:
            parsed = self._parse_json_object(arguments_obj)
            if parsed is not None:
                input_payload = parsed
            else:
                input_payload = {"arguments": arguments_obj}

        query_obj = payload.get("query")
        if isinstance(query_obj, str) and query_obj:
            input_payload = {**input_payload, "query": query_obj}

        return {
            "block_type": "tool_use",
            "tool_name": tool_name,
            "tool_id": tool_id,
            "input": input_payload,
        }

    def _tool_result_from_payload(
        self,
        payload_type: str,
        payload: dict[str, object],
        source_line_index: int,
    ) -> dict[str, object] | None:
        tool_use_id_obj = payload.get("call_id") or payload.get("tool_call_id") or payload.get("id")
        tool_use_id: str | None
        if isinstance(tool_use_id_obj, str) and tool_use_id_obj:
            tool_use_id = tool_use_id_obj
        else:
            tool_use_id = f"{payload_type}-{source_line_index}"

        output_obj = payload.get("output")
        content_obj = payload.get("content") if output_obj is None else output_obj
        if content_obj is None:
            content_obj = payload.get("result")
        content_text = self._stringify_content(content_obj)

        is_error = bool(payload.get("is_error", False))
        status = payload.get("status")
        if not is_error and isinstance(status, str) and status.lower() in {"error", "failed"}:
            is_error = True

        return {
            "block_type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content_text,
            "is_error": is_error,
        }

    @staticmethod
    def _stringify_content(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False, default=str))
            return "\n".join(parts)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, default=str)
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _parse_json_object(value: str) -> dict[str, object] | None:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return cast(dict[str, object], parsed)
        return {"value": cast(object, parsed)}

    def _extract_meta_field(
        self, session_meta: dict[str, object] | None, path: str
    ) -> object | None:
        if session_meta is None:
            return None

        current: object = session_meta
        for key in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(key)
            if current is None:
                return None
        return current

    @staticmethod
    def _first_session_meta(lines: list[dict[str, object]]) -> dict[str, object] | None:
        for line in lines:
            if line.get("type") != "session_meta":
                continue
            payload = line.get("payload")
            if isinstance(payload, dict):
                return cast(dict[str, object], payload)
        return None

    @staticmethod
    def _first_valid_timestamp(lines: list[dict[str, object]]) -> datetime | None:
        for line in lines:
            ts = parse_timestamp(line)
            if ts is not None:
                return ts
        return None

    @staticmethod
    def _last_valid_timestamp(lines: list[dict[str, object]]) -> datetime | None:
        for line in reversed(lines):
            ts = parse_timestamp(line)
            if ts is not None:
                return ts
        return None

    @staticmethod
    def _session_id_from_path(fpath: Path) -> str | None:
        stem = fpath.stem
        if not stem.startswith("rollout-"):
            return None

        parts = stem.split("-")
        if len(parts) < 6:
            return None
        return "-".join(parts[-5:])

    @staticmethod
    def _parse_timestamp_from_path(fpath: Path) -> datetime | None:
        stem = fpath.stem
        if not stem.startswith("rollout-"):
            return None
        rest = stem[len("rollout-") :]
        dt_part = rest[:19]
        try:
            normalized = dt_part[:10] + "T" + dt_part[11:].replace("-", ":")
            return datetime.fromisoformat(normalized).replace(tzinfo=UTC)
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _parse_history_timestamp(ts: object) -> datetime | None:
        if not isinstance(ts, (int, float)):
            return None
        try:
            seconds = ts / 1000 if ts > 10_000_000_000 else ts
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (ValueError, OSError):
            return None

    @staticmethod
    def _history_sort_key(entry: dict[str, object]) -> float:
        ts = entry.get("ts")
        if isinstance(ts, (int, float)):
            return float(ts)
        return 0.0

    @staticmethod
    def _normalize_project_path(cwd: str) -> str:
        home = str(Path.home())
        if cwd.startswith(home + "/"):
            return "~/" + cwd[len(home) + 1 :]
        if cwd == home:
            return "~"
        return cwd

    def _last_sync_epoch(self) -> float:
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        if not last_sync:
            return 0.0
        try:
            return datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp()
        except ValueError:
            return 0.0
