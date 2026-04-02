from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.observe.catalog import discovered_roots, get_source


def _default_source_roots() -> tuple[Path, ...]:
    spec = get_source("gemini-cli")
    if spec is None:
        return ()
    return tuple(discovered_roots(spec))


class GeminiCliObserveAdapter(ObserveAdapter):
    source = "gemini-cli"

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

            if self._is_chat_file(path):
                session = self._parse_chat_file(path)
            elif self._is_checkpoint_file(path):
                session = self._parse_checkpoint_file(path)
            else:
                session = None

            if session is None or not session.turns or session.session_id in seen_session_ids:
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
            if self._is_chat_file(resolved) or self._is_checkpoint_file(resolved):
                return [resolved]
            return []

        if not resolved.is_dir():
            return []

        chat_dir, checkpoint_dir = self._artifact_dirs_for_candidate(resolved)
        discovered: list[Path] = []
        seen: set[Path] = set()

        for directory, nested in ((chat_dir, True), (checkpoint_dir, False)):
            if directory is None or not directory.is_dir():
                continue
            iterator = directory.rglob("*.json") if nested else directory.glob("*.json")
            for path in iterator:
                try:
                    artifact = path.resolve()
                except OSError:
                    continue
                if artifact in seen:
                    continue
                if not (self._is_chat_file(artifact) or self._is_checkpoint_file(artifact)):
                    continue
                seen.add(artifact)
                discovered.append(artifact)

        return sorted(discovered, key=self._candidate_sort_key)

    def _artifact_dirs_for_candidate(self, directory: Path) -> tuple[Path | None, Path | None]:
        parts = directory.parts
        if directory.name == "chats":
            return directory, None
        if directory.name == "checkpoints":
            return None, directory

        try:
            tmp_index = parts.index("tmp")
        except ValueError:
            tmp_index = -1

        if tmp_index != -1 and len(parts) >= tmp_index + 2:
            project_root = Path(*parts[: tmp_index + 2])
            if directory == project_root:
                return project_root / "chats", project_root / "checkpoints"

        if directory.name == ".gemini" or "tmp" not in parts:
            return directory / "tmp", None

        return None, None

    def _parse_chat_file(self, path: Path) -> ObservedSession | None:
        payload = self._load_json_file(path)
        if not isinstance(payload, dict):
            return None

        session_id = self._as_str(payload.get("sessionId")) or self._chat_session_id_from_path(path)
        messages = payload.get("messages")
        if not session_id or not isinstance(messages, list):
            return None

        start_time = self._parse_ts(payload.get("startTime"))
        end_time = self._parse_ts(payload.get("lastUpdated"))
        project_hash = self._project_hash_from_path(path)
        parent_session_id = self._chat_parent_session_id(path)
        is_subagent = parent_session_id is not None or self._as_str(payload.get("kind")) == "subagent"
        turns = self._turns_from_chat_messages(messages, start_time)
        if not turns:
            return None

        if start_time is None:
            start_time = turns[0].timestamp
        if end_time is None:
            end_time = max((turn.timestamp for turn in turns), default=start_time)

        metadata: dict[str, Any] = {
            "artifact_family": "chat",
            "project_hash": project_hash,
            "source_root": str(path.parent),
            "summary": self._as_str(payload.get("summary")),
            "kind": self._as_str(payload.get("kind")),
        }
        directories = payload.get("directories")
        if isinstance(directories, list):
            metadata["directories"] = [
                item for item in directories if isinstance(item, str) and item
            ]

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            parent_session_id=parent_session_id,
            turns=turns,
            metadata={key: value for key, value in metadata.items() if value is not None},
            is_subagent=is_subagent,
            source_instance_id=str(path),
        )

    def _parse_checkpoint_file(self, path: Path) -> ObservedSession | None:
        payload = self._load_json_file(path)
        if not isinstance(payload, dict):
            return None

        if isinstance(payload.get("messages"), list):
            session = self._parse_chat_file(path)
            if session is None:
                return None
            checkpoint_id = self._checkpoint_session_id(path, payload)
            session.session_id = checkpoint_id
            session.metadata["artifact_family"] = "checkpoint"
            session.metadata["checkpoint_name"] = path.stem
            session.metadata["checkpoint_format"] = "conversation"
            session.source_instance_id = str(path)
            return session

        sequence_base = self._parse_ts(payload.get("timestamp"))
        turns = self._turns_from_client_history(payload.get("clientHistory"), sequence_base)
        if not turns:
            turns = self._turns_from_ui_history(payload.get("history"), sequence_base)

        if not turns and isinstance(payload.get("toolCall"), dict):
            tool_call = payload["toolCall"]
            turns = [
                ObservedTurn(
                    role="assistant",
                    content="",
                    timestamp=self._fallback_timestamp(sequence_base, 0),
                    tool_calls=[self._tool_use_block_from_tool_call(tool_call, "checkpoint:0")],
                    metadata={"source_event_type": "checkpoint_tool_call"},
                )
            ]
        elif turns and isinstance(payload.get("toolCall"), dict):
            last_assistant = next((turn for turn in reversed(turns) if turn.role == "assistant"), None)
            if last_assistant is None:
                last_assistant = ObservedTurn(
                    role="assistant",
                    content="",
                    timestamp=turns[-1].timestamp,
                    metadata={"source_event_type": "checkpoint_tool_call"},
                )
                turns.append(last_assistant)
            last_assistant.tool_calls.append(
                self._tool_use_block_from_tool_call(payload["toolCall"], f"checkpoint:{len(last_assistant.tool_calls)}")
            )

        if not turns:
            return None

        session_id = self._checkpoint_session_id(path, payload)
        start_time = min(turn.timestamp for turn in turns)
        end_time = max(turn.timestamp for turn in turns)
        metadata: dict[str, Any] = {
            "artifact_family": "checkpoint",
            "project_hash": self._project_hash_from_path(path),
            "checkpoint_name": path.stem,
            "message_id": self._as_str(payload.get("messageId")),
            "commit_hash": self._as_str(payload.get("commitHash")),
            "has_client_history": isinstance(payload.get("clientHistory"), list),
            "has_history": isinstance(payload.get("history"), list),
            "source_root": str(path.parent),
        }

        return ObservedSession(
            session_id=session_id,
            source_path=path,
            start_time=start_time,
            end_time=end_time,
            turns=turns,
            metadata={key: value for key, value in metadata.items() if value is not None},
            source_instance_id=str(path),
        )

    def _turns_from_chat_messages(
        self,
        messages: list[Any],
        base_time: datetime | None,
    ) -> list[ObservedTurn]:
        turns: list[ObservedTurn] = []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            timestamp = self._parse_ts(message.get("timestamp")) or self._fallback_timestamp(
                base_time,
                index,
            )
            message_type = self._as_str(message.get("type"))

            if message_type == "user":
                content = self._part_list_to_text(message.get("content"))
                if not content:
                    continue
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                        metadata={
                            "source_message_index": index,
                            "source_event_type": "user",
                        },
                    )
                )
                continue

            if message_type != "gemini":
                continue

            turn = ObservedTurn(
                role="assistant",
                content=self._assistant_message_text(message),
                timestamp=timestamp,
                metadata={
                    "source_message_index": index,
                    "source_event_type": "gemini",
                    "model": self._as_str(message.get("model")),
                },
            )
            tokens = message.get("tokens")
            if isinstance(tokens, dict):
                turn.metadata["tokens"] = tokens

            for offset, tool_call in enumerate(message.get("toolCalls") or []):
                block = self._tool_blocks_from_record(tool_call, f"{index}:{offset}")
                if block is None:
                    continue
                turn.tool_calls.extend(block)

            if not turn.content and not turn.tool_calls:
                continue
            turns.append(turn)

        return turns

    def _turns_from_client_history(
        self,
        history: Any,
        base_time: datetime | None,
    ) -> list[ObservedTurn]:
        if not isinstance(history, list):
            return []

        turns: list[ObservedTurn] = []
        current_assistant: ObservedTurn | None = None
        pending_tool_calls: dict[str, dict[str, Any]] = {}

        for index, item in enumerate(history):
            if not isinstance(item, dict):
                continue
            role = self._as_str(item.get("role"))
            parts = item.get("parts")
            timestamp = self._fallback_timestamp(base_time, index)
            text_parts, tool_uses, tool_results = self._decode_content_parts(parts, f"client:{index}")

            if role == "user":
                if text_parts:
                    turns.append(
                        ObservedTurn(
                            role="user",
                            content="\n\n".join(text_parts),
                            timestamp=timestamp,
                            metadata={
                                "source_event_type": "client_history_user",
                                "source_message_index": index,
                            },
                        )
                    )
                    current_assistant = None

                if tool_results:
                    current_assistant = self._ensure_assistant_turn(
                        turns,
                        current_assistant,
                        timestamp,
                        index,
                        "client_history_tool_result",
                    )
                    for block in tool_results:
                        matched = pending_tool_calls.get(self._as_str(block.get("tool_use_id")) or "")
                        if matched is not None and "tool_name" not in block:
                            block["tool_name"] = matched.get("tool_name")
                        current_assistant.tool_calls.append(block)
                continue

            if role not in {"model", "assistant"}:
                continue

            current_assistant = self._ensure_assistant_turn(
                turns,
                current_assistant,
                timestamp,
                index,
                "client_history_model",
            )
            if text_parts:
                self._append_turn_content(current_assistant, "\n\n".join(text_parts))
            for block in tool_uses:
                tool_id = self._as_str(block.get("tool_id"))
                if tool_id:
                    pending_tool_calls[tool_id] = block
                current_assistant.tool_calls.append(block)
            for block in tool_results:
                matched = pending_tool_calls.get(self._as_str(block.get("tool_use_id")) or "")
                if matched is not None and "tool_name" not in block:
                    block["tool_name"] = matched.get("tool_name")
                current_assistant.tool_calls.append(block)

        return [turn for turn in turns if turn.content or turn.tool_calls]

    def _turns_from_ui_history(self, history: Any, base_time: datetime | None) -> list[ObservedTurn]:
        if not isinstance(history, list):
            return []

        turns: list[ObservedTurn] = []
        for index, item in enumerate(history):
            if not isinstance(item, dict):
                continue
            item_type = self._as_str(item.get("type"))
            timestamp = self._fallback_timestamp(base_time, index)

            if item_type == "user":
                content = self._ui_history_text(item)
                if not content:
                    continue
                turns.append(
                    ObservedTurn(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                        metadata={"source_event_type": "ui_history_user", "source_message_index": index},
                    )
                )
                continue

            if item_type in {"gemini", "assistant"}:
                content = self._ui_history_text(item)
                if not content:
                    continue
                turns.append(
                    ObservedTurn(
                        role="assistant",
                        content=content,
                        timestamp=timestamp,
                        metadata={"source_event_type": "ui_history_assistant", "source_message_index": index},
                    )
                )
                continue

            if item_type != "tool_group":
                continue

            assistant = ObservedTurn(
                role="assistant",
                content="",
                timestamp=timestamp,
                metadata={"source_event_type": "ui_history_tool_group", "source_message_index": index},
            )
            for offset, tool in enumerate(item.get("tools") or []):
                if not isinstance(tool, dict):
                    continue
                call_id = self._as_str(tool.get("callId")) or f"ui:{index}:{offset}"
                name = self._as_str(tool.get("name")) or "tool"
                assistant.tool_calls.append(
                    {
                        "block_type": "tool_use",
                        "tool_name": name,
                        "tool_id": call_id,
                        "input": self._as_dict(tool.get("args")) or {},
                        "status": self._as_str(tool.get("status")),
                    }
                )
                result = tool.get("result")
                if result is not None:
                    assistant.tool_calls.append(
                        {
                            "block_type": "tool_result",
                            "tool_use_id": call_id,
                            "tool_name": name,
                            "content": self._part_list_to_text(result),
                            "is_error": self._status_is_error(tool.get("status")),
                        }
                    )
            if assistant.tool_calls:
                turns.append(assistant)

        return turns

    def _assistant_message_text(self, message: dict[str, Any]) -> str:
        pieces: list[str] = []
        display_content = self._part_list_to_text(message.get("displayContent"))
        content = self._part_list_to_text(message.get("content"))
        if display_content:
            pieces.append(display_content)
        elif content:
            pieces.append(content)

        thoughts = message.get("thoughts")
        if isinstance(thoughts, list):
            thought_lines = [
                self._thought_to_text(item)
                for item in thoughts
                if isinstance(item, dict)
            ]
            thought_text = "\n".join(line for line in thought_lines if line)
            if thought_text:
                pieces.append(f"[thoughts]\n{thought_text}")

        return "\n\n".join(piece for piece in pieces if piece)

    def _thought_to_text(self, thought: dict[str, Any]) -> str:
        subject = self._as_str(thought.get("subject"))
        description = self._as_str(thought.get("description"))
        if subject and description:
            return f"{subject}: {description}"
        return subject or description or ""

    def _tool_blocks_from_record(
        self,
        value: Any,
        fallback_id: str,
    ) -> list[dict[str, Any]] | None:
        if not isinstance(value, dict):
            return None

        tool_id = self._as_str(value.get("id")) or fallback_id
        tool_name = self._as_str(value.get("name")) or "tool"
        block: dict[str, Any] = {
            "block_type": "tool_use",
            "tool_name": tool_name,
            "tool_id": tool_id,
            "input": self._as_dict(value.get("args")) or {},
        }
        status = self._as_str(value.get("status"))
        if status:
            block["status"] = status

        blocks = [block]
        if "result" in value:
            blocks.append(
                {
                    "block_type": "tool_result",
                    "tool_use_id": tool_id,
                    "tool_name": tool_name,
                    "content": self._part_list_to_text(value.get("result")),
                    "is_error": self._status_is_error(status),
                }
            )
        return blocks

    def _tool_use_block_from_tool_call(self, value: dict[str, Any], fallback_id: str) -> dict[str, Any]:
        return {
            "block_type": "tool_use",
            "tool_name": self._as_str(value.get("name")) or "tool",
            "tool_id": fallback_id,
            "input": self._as_dict(value.get("args")) or {},
        }

    def _decode_content_parts(
        self,
        value: Any,
        prefix: str,
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []

        for offset, part in enumerate(self._part_list(value)):
            if not isinstance(part, dict):
                if isinstance(part, str) and part:
                    text_parts.append(part)
                continue

            text = self._as_str(part.get("text"))
            if text:
                text_parts.append(text)
                continue

            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                call_id = self._as_str(function_call.get("id")) or f"{prefix}:call:{offset}"
                tool_uses.append(
                    {
                        "block_type": "tool_use",
                        "tool_name": self._as_str(function_call.get("name")) or "tool",
                        "tool_id": call_id,
                        "input": self._as_dict(function_call.get("args")) or {},
                    }
                )
                continue

            function_response = part.get("functionResponse")
            if isinstance(function_response, dict):
                tool_results.append(
                    {
                        "block_type": "tool_result",
                        "tool_use_id": self._as_str(function_response.get("id")),
                        "tool_name": self._as_str(function_response.get("name")),
                        "content": self._part_list_to_text(function_response.get("response")),
                        "is_error": self._response_is_error(function_response.get("response")),
                    }
                )
                continue

            executable = part.get("executableCode")
            if isinstance(executable, dict):
                code = self._as_str(executable.get("code"))
                if code:
                    text_parts.append(f"[code]\n{code}")
                continue

            execution_result = part.get("codeExecutionResult")
            if isinstance(execution_result, dict):
                output = self._as_str(execution_result.get("output"))
                if output:
                    text_parts.append(f"[execution_result]\n{output}")
                continue

            if "inlineData" in part:
                text_parts.append("[inline_data]")
                continue
            if "fileData" in part:
                text_parts.append("[file_data]")

        return text_parts, tool_uses, tool_results

    def _ensure_assistant_turn(
        self,
        turns: list[ObservedTurn],
        current: ObservedTurn | None,
        timestamp: datetime,
        index: int,
        event_type: str,
    ) -> ObservedTurn:
        if current is not None:
            return current
        turn = ObservedTurn(
            role="assistant",
            content="",
            timestamp=timestamp,
            metadata={"source_event_type": event_type, "source_message_index": index},
        )
        turns.append(turn)
        return turn

    def _append_turn_content(self, turn: ObservedTurn, text: str) -> None:
        if not text:
            return
        if turn.content:
            turn.content += "\n\n"
        turn.content += text

    def _part_list_to_text(self, value: Any) -> str:
        text_parts, _tool_uses, _tool_results = self._decode_content_parts(value, "part")
        if text_parts:
            return "\n\n".join(part for part in text_parts if part)
        if isinstance(value, dict):
            text = self._as_str(value.get("text"))
            if text:
                return text
        return ""

    def _part_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            parts = value.get("parts")
            if isinstance(parts, list):
                return parts
            return [value]
        return [value]

    def _ui_history_text(self, item: dict[str, Any]) -> str:
        for key in ("text", "content"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _project_hash_from_path(self, path: Path) -> str | None:
        parts = path.parts
        try:
            tmp_index = parts.index("tmp")
        except ValueError:
            return None
        if len(parts) <= tmp_index + 1:
            return None
        return parts[tmp_index + 1]

    def _chat_parent_session_id(self, path: Path) -> str | None:
        parts = path.parts
        try:
            chats_index = parts.index("chats")
        except ValueError:
            return None
        if len(parts) == chats_index + 2:
            return None
        if len(parts) > chats_index + 2:
            return parts[chats_index + 1]
        return None

    def _chat_session_id_from_path(self, path: Path) -> str:
        parent = self._chat_parent_session_id(path)
        if parent:
            return path.stem
        return path.stem.removeprefix("session-")

    def _checkpoint_session_id(self, path: Path, payload: dict[str, Any]) -> str:
        base = self._as_str(payload.get("sessionId")) or path.stem
        project_hash = self._project_hash_from_path(path)
        if project_hash:
            return f"{project_hash}:checkpoint:{base}"
        return f"checkpoint:{base}"

    def _candidate_sort_key(self, path: Path) -> tuple[int, str]:
        priority = 0 if self._is_chat_file(path) else 1 if self._is_checkpoint_file(path) else 99
        return priority, str(path)

    def _is_chat_file(self, path: Path) -> bool:
        if path.suffix != ".json" or not path.is_file():
            return False
        parts = path.parts
        try:
            tmp_index = parts.index("tmp")
        except ValueError:
            return False
        if len(parts) <= tmp_index + 2:
            return False
        return "chats" in parts[tmp_index + 2 :]

    def _is_checkpoint_file(self, path: Path) -> bool:
        if path.suffix != ".json" or not path.is_file():
            return False
        parts = path.parts
        try:
            tmp_index = parts.index("tmp")
        except ValueError:
            return False
        if len(parts) <= tmp_index + 2:
            return False
        return "checkpoints" in parts[tmp_index + 2 :]

    def _load_json_file(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _fallback_timestamp(base: datetime | None, sequence: int) -> datetime:
        origin = base or datetime.fromtimestamp(0, tz=UTC)
        return origin + timedelta(microseconds=sequence)

    @staticmethod
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any] | None:
        return value if isinstance(value, dict) else None

    @staticmethod
    def _status_is_error(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return value.lower() not in {"success", "succeeded", "ok", "completed"}

    @staticmethod
    def _response_is_error(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        success = value.get("success")
        if isinstance(success, bool):
            return not success
        exit_code = value.get("exit_code")
        if isinstance(exit_code, (int, float)) and int(exit_code) != 0:
            return True
        error = value.get("error")
        return error not in {None, "", False}

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
