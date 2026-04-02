from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.observe.catalog import discovered_roots, get_source


def _default_source_roots() -> tuple[Path, ...]:
    spec = get_source("claude-code")
    if spec is None:
        return ()
    return tuple(discovered_roots(spec))


class ClaudeCodeObserveAdapter(ObserveAdapter):
    source = "claude-code"

    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        source_roots: Iterable[Path | str] | None = None,
    ):
        super().__init__(db, user_id)
        roots = source_roots or _default_source_roots()
        self.source_roots = tuple(Path(root).expanduser() for root in roots)

    def discover(self) -> list[Path]:
        discovered: list[Path] = []
        seen: set[Path] = set()
        for root in self.source_roots:
            try:
                resolved_root = root.resolve()
            except OSError:
                continue
            if resolved_root.is_file() and resolved_root.suffix == ".jsonl":
                if resolved_root not in seen:
                    seen.add(resolved_root)
                    discovered.append(resolved_root)
                continue
            if not resolved_root.is_dir():
                continue
            for path in resolved_root.rglob("*.jsonl"):
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen or not resolved.is_file():
                    continue
                seen.add(resolved)
                discovered.append(resolved)
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
            path = Path(candidate).expanduser()
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.is_file() and resolved.suffix == ".jsonl":
                if resolved not in seen:
                    seen.add(resolved)
                    normalized.append(resolved)
                continue
            if not resolved.is_dir():
                continue
            for child in resolved.rglob("*.jsonl"):
                try:
                    child_resolved = child.resolve()
                except OSError:
                    continue
                if child_resolved in seen or not child_resolved.is_file():
                    continue
                seen.add(child_resolved)
                normalized.append(child_resolved)
        return sorted(normalized, key=lambda path: str(path))

    def _parse_session_file(self, path: Path) -> ObservedSession | None:
        parts = set(path.parts)
        if "transcripts" in parts:
            return self._parse_transcript_file(path)
        return self._parse_project_file(path)

    def _parse_transcript_file(self, path: Path) -> ObservedSession | None:
        session_id = path.stem
        turns: list[ObservedTurn] = []
        current_assistant_turn: ObservedTurn | None = None
        pending_tool_ids: deque[str] = deque()
        start_time: datetime | None = None
        end_time: datetime | None = None

        for line_index, record in self._iter_jsonl(path):
            timestamp = self._parse_ts(record.get("timestamp"))
            start_time = timestamp if start_time is None or timestamp < start_time else start_time
            end_time = timestamp if end_time is None or timestamp > end_time else end_time
            record_type = self._as_str(record.get("type")) or ""

            if record_type == "user":
                content = self._as_str(record.get("content")) or ""
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                        metadata={
                            "source_event_type": "user",
                            "source_line_index": line_index,
                        },
                    )
                )
                current_assistant_turn = None
                pending_tool_ids.clear()
                continue

            if record_type not in {"tool_use", "tool_result"}:
                continue

            if current_assistant_turn is None:
                current_assistant_turn = ObservedTurn(
                    role="assistant",
                    content="",
                    timestamp=timestamp,
                    metadata={
                        "source_event_type": "transcript_tool_trace",
                        "source_line_index": line_index,
                    },
                )
                turns.append(current_assistant_turn)

            if timestamp < current_assistant_turn.timestamp:
                current_assistant_turn.timestamp = timestamp

            tool_name = self._as_str(record.get("tool_name"))
            tool_input = self._as_dict(record.get("tool_input"))
            tool_id = f"{session_id}:tool:{line_index}"

            if record_type == "tool_use":
                current_assistant_turn.tool_calls.append(
                    {
                        "block_type": "tool_use",
                        "tool_name": tool_name,
                        "tool_id": tool_id,
                        "input": tool_input,
                    }
                )
                pending_tool_ids.append(tool_id)
                continue

            tool_output = record.get("tool_output")
            matched_tool_id = pending_tool_ids.popleft() if pending_tool_ids else None
            current_assistant_turn.tool_calls.append(
                {
                    "block_type": "tool_result",
                    "tool_use_id": matched_tool_id,
                    "content": self._stringify_tool_content(tool_output),
                    "is_error": self._tool_output_is_error(tool_output),
                }
            )

        if not turns or start_time is None:
            return None

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            turns=turns,
            metadata={
                "artifact_family": "transcript",
                "source_root": str(path.parent),
            },
            source_instance_id=str(path),
        )

    def _parse_project_file(self, path: Path) -> ObservedSession | None:
        session_id = path.stem
        parent_session_id: str | None = None
        is_subagent = "subagents" in path.parts
        turns: list[ObservedTurn] = []
        current_assistant_turn: ObservedTurn | None = None
        start_time: datetime | None = None
        end_time: datetime | None = None
        project: str | None = None
        agent_id: str | None = None
        agent_slug: str | None = None
        metadata: dict[str, Any] = {
            "artifact_family": "project",
            "is_sidechain": is_subagent,
        }

        if is_subagent:
            for parent in path.parents:
                if parent.name == "subagents":
                    parent_session_id = parent.parent.name
                    break

        for line_index, record in self._iter_jsonl(path):
            timestamp = self._parse_ts(record.get("timestamp"))
            start_time = timestamp if start_time is None or timestamp < start_time else start_time
            end_time = timestamp if end_time is None or timestamp > end_time else end_time

            session_id = self._as_str(record.get("sessionId")) or session_id
            if parent_session_id is None and is_subagent:
                parent_session_id = self._as_str(record.get("sessionId")) or parent_session_id

            cwd = self._as_str(record.get("cwd"))
            if cwd and not project:
                project = cwd
            version = self._as_str(record.get("version"))
            git_branch = self._as_str(record.get("gitBranch"))
            if version:
                metadata.setdefault("version", version)
            if git_branch:
                metadata.setdefault("git_branch", git_branch)
            if cwd:
                metadata.setdefault("cwd", cwd)
            if record.get("isSidechain") is True:
                metadata["is_sidechain"] = True
            if self._as_str(record.get("promptId")):
                metadata.setdefault("first_prompt_id", self._as_str(record.get("promptId")))

            record_agent_id = self._as_str(record.get("agentId"))
            record_slug = self._as_str(record.get("slug"))
            if record_agent_id:
                agent_id = record_agent_id
            if record_slug:
                agent_slug = record_slug

            record_type = self._as_str(record.get("type")) or ""
            if record_type == "queue-operation":
                metadata["queue_operation_count"] = (
                    int(metadata.get("queue_operation_count", 0)) + 1
                )
                continue
            if record_type == "last-prompt":
                last_prompt = self._as_str(record.get("lastPrompt"))
                if last_prompt:
                    metadata["last_prompt"] = last_prompt
                continue

            message = record.get("message")
            if not isinstance(message, dict):
                continue

            role = self._as_str(message.get("role"))
            if role == "assistant":
                turn = self._assistant_turn_from_project_record(message, timestamp, line_index)
                if turn is None:
                    continue
                turns.append(turn)
                current_assistant_turn = turn
                continue

            if role != "user":
                continue

            text_blocks, tool_result_blocks = self._user_blocks_from_project_record(message)
            if text_blocks:
                turns.append(
                    ObservedTurn(
                        role="user",
                        content="\n\n".join(text_blocks).strip(),
                        timestamp=timestamp,
                        metadata={
                            "source_event_type": "user",
                            "source_line_index": line_index,
                            **self._compact_dict(
                                {
                                    "prompt_id": self._as_str(record.get("promptId")),
                                    "permission_mode": self._as_str(record.get("permissionMode")),
                                    "user_type": self._as_str(record.get("userType")),
                                    "entrypoint": self._as_str(record.get("entrypoint")),
                                }
                            ),
                        },
                    )
                )
                current_assistant_turn = None

            if tool_result_blocks:
                if current_assistant_turn is None:
                    current_assistant_turn = ObservedTurn(
                        role="assistant",
                        content="",
                        timestamp=timestamp,
                        metadata={
                            "source_event_type": "tool_result_only",
                            "source_line_index": line_index,
                        },
                    )
                    turns.append(current_assistant_turn)
                current_assistant_turn.tool_calls.extend(tool_result_blocks)

        if not turns or start_time is None:
            return None

        normalized_session_id = session_id
        if is_subagent and agent_id:
            normalized_session_id = f"{session_id}:agent:{agent_id}"

        return ObservedSession(
            session_id=normalized_session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            project=project,
            parent_session_id=parent_session_id,
            turns=turns,
            metadata=self._compact_dict(metadata),
            is_subagent=is_subagent,
            agent_id=agent_id,
            agent_slug=agent_slug,
            source_instance_id=str(path),
        )

    def _assistant_turn_from_project_record(
        self,
        message: dict[str, Any],
        timestamp: datetime,
        line_index: int,
    ) -> ObservedTurn | None:
        content_blocks = message.get("content")
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        if isinstance(content_blocks, list):
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                block_type = self._as_str(block.get("type")) or ""
                if block_type == "text":
                    text = self._as_str(block.get("text"))
                    if text:
                        content_parts.append(text)
                elif block_type == "thinking":
                    thinking = self._as_str(block.get("thinking"))
                    if thinking:
                        content_parts.append(f"[thinking] {thinking}")
                elif block_type == "tool_use":
                    tool_calls.append(
                        {
                            "block_type": "tool_use",
                            "tool_name": self._as_str(block.get("name")),
                            "tool_id": self._as_str(block.get("id")),
                            "input": self._as_dict(block.get("input")),
                        }
                    )
        elif isinstance(content_blocks, str) and content_blocks:
            content_parts.append(content_blocks)

        if not content_parts and not tool_calls:
            return None

        metadata = self._compact_dict(
            {
                "source_event_type": "assistant",
                "source_line_index": line_index,
                "model": self._as_str(message.get("model")),
                "stop_reason": self._as_str(message.get("stop_reason")),
                "usage": self._as_dict(message.get("usage")) or None,
            }
        )

        return ObservedTurn(
            role="assistant",
            content="\n\n".join(part for part in content_parts if part).strip(),
            timestamp=timestamp,
            tool_calls=tool_calls,
            metadata=metadata,
        )

    def _user_blocks_from_project_record(
        self,
        message: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        content = message.get("content")
        text_blocks: list[str] = []
        tool_result_blocks: list[dict[str, Any]] = []

        if isinstance(content, str):
            if content:
                text_blocks.append(content)
            return text_blocks, tool_result_blocks

        if not isinstance(content, list):
            return text_blocks, tool_result_blocks

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = self._as_str(block.get("type")) or ""
            if block_type == "text":
                text = self._as_str(block.get("text"))
                if text:
                    text_blocks.append(text)
            elif block_type == "tool_result":
                tool_result_blocks.append(
                    {
                        "block_type": "tool_result",
                        "tool_use_id": self._as_str(block.get("tool_use_id")),
                        "content": self._stringify_tool_content(block.get("content")),
                        "is_error": bool(block.get("is_error")),
                    }
                )
        return text_blocks, tool_result_blocks

    def _iter_jsonl(self, path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle, start=1):
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(record, dict):
                        yield index, record
        except OSError:
            return

    @staticmethod
    def _tool_output_is_error(tool_output: Any) -> bool:
        if isinstance(tool_output, dict):
            if tool_output.get("is_error"):
                return True
            stderr = tool_output.get("stderr")
            if isinstance(stderr, str) and stderr.strip():
                return False
        return False

    @staticmethod
    def _stringify_tool_content(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if item is not None}

    @staticmethod
    def _parse_ts(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
            except ValueError:
                pass
        if isinstance(value, int | float):
            ts = float(value)
            if ts > 1e18:
                ts /= 1e9
            elif ts > 1e15:
                ts /= 1e6
            elif ts > 1e12:
                ts /= 1e3
            return datetime.fromtimestamp(ts, tz=UTC)
        return datetime.now(UTC)
