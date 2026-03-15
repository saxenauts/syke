from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

from syke.config_file import expand_path
from syke.db import SykeDB
from syke.ingestion.constants import ROLE_ASSISTANT, ROLE_USER
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn
from syke.ingestion.parsers import (
    decode_project_dir,
    extract_text_content,
    extract_tool_blocks,
    measure_content,
    parse_timestamp,
    read_jsonl,
)

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter(ObserveAdapter):
    source: str = "claude-code"

    def __init__(self, db: SykeDB, user_id: str):
        super().__init__(db, user_id)
        from syke.ingestion.descriptor import (
            load_descriptor,  # pyright: ignore[reportMissingImports]
        )

        self._descriptor = load_descriptor(
            Path(__file__).parent / "descriptors" / "claude-code.toml"
        )
        self._file_metadata: dict[Path, dict[str, str | None]] = {}

    @override
    def discover(self) -> list[Path]:
        discover_cfg = self._descriptor.discover
        if discover_cfg is None:
            return []

        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        last_sync_epoch = (
            datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp() if last_sync else 0.0
        )

        discovered: list[Path] = []
        seen_stems: set[str] = set()
        self._file_metadata = {}

        sorted_roots = sorted(discover_cfg.roots, key=lambda root: root.priority, reverse=True)
        for root in sorted_roots:
            root_path = expand_path(root.path)
            if not root_path.exists():
                continue

            for pattern in root.include:
                for fpath in sorted(root_path.glob(pattern), key=os.path.getmtime):
                    if not fpath.is_file():
                        continue

                    if root_path.name == "projects":
                        try:
                            project_dir = fpath.relative_to(root_path).parts[0]
                        except (ValueError, IndexError):
                            continue

                        seen_stems.add(fpath.stem)
                        if fpath.stat().st_mtime < last_sync_epoch:
                            continue

                        discovered.append(fpath)
                        self._file_metadata[fpath] = {
                            "project": decode_project_dir(project_dir),
                            "store": "project",
                        }
                        continue

                    if root_path.name == "transcripts":
                        if fpath.stem in seen_stems:
                            continue
                        if fpath.stat().st_mtime < last_sync_epoch:
                            continue

                        discovered.append(fpath)
                        self._file_metadata[fpath] = {
                            "project": None,
                            "store": "transcript",
                        }

        return discovered

    @override
    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        for fpath in self.discover():
            if since and fpath.stat().st_mtime < since:
                continue
            try:
                session = self._parse_session(fpath)
                if session is not None:
                    yield session
            except Exception as exc:
                logger.warning("Failed to parse session %s: %s", fpath.name, exc)

    def _parse_session(self, fpath: Path) -> ObservedSession | None:
        lines = read_jsonl(fpath)
        if not lines:
            return None

        first_line = lines[0]
        session_id_raw = first_line.get("sessionId")
        session_id_from_field = isinstance(session_id_raw, str) and bool(session_id_raw)
        session_id: str = str(session_id_raw) if session_id_from_field else fpath.stem

        start_time = self._first_valid_timestamp(lines)
        if start_time is None:
            return None
        end_time = self._last_valid_timestamp(lines) or start_time

        parent_session_id = self._first_string(lines, "parentSessionId")
        agent_id = self._first_string(lines, "agentId")
        agent_slug = self._first_string(lines, "agentSlug")
        is_subagent = agent_id is not None

        turns: list[ObservedTurn] = []
        turn_counter = {ROLE_USER: 0, ROLE_ASSISTANT: 0}
        content_chars_total = 0
        harness_line_types: dict[str, int] = {}

        for idx, line in enumerate(lines):
            role_obj = line.get("type")
            if role_obj not in (ROLE_USER, ROLE_ASSISTANT):
                line_type = str(role_obj) if role_obj else "unknown"
                harness_line_types[line_type] = harness_line_types.get(line_type, 0) + 1
                continue
            role = str(role_obj)

            normalized_line = line
            if "message" not in line and isinstance(line.get("content"), str):
                normalized_line = {**line, "message": None}

            content = extract_text_content(normalized_line).strip()
            tool_blocks = extract_tool_blocks(normalized_line)
            if not content and not tool_blocks:
                continue

            ts = parse_timestamp(line)
            ts_inferred = ts is None
            timestamp = ts or start_time

            uuid = self._line_uuid(line, idx)
            parent_uuid = self._line_parent_uuid(line)

            turn_meta: dict[str, object] = {
                "uuid": uuid,
                "parent_uuid": parent_uuid,
                "source_line_index": idx,
            }
            if ts_inferred:
                turn_meta["timestamp_inferred"] = True

            msg = line.get("message", {})
            if isinstance(msg, dict):
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    turn_meta["usage"] = usage
                model = msg.get("model")
                if isinstance(model, str) and model:
                    turn_meta["model"] = model
                stop_reason = msg.get("stop_reason")
                if isinstance(stop_reason, str) and stop_reason:
                    turn_meta["stop_reason"] = stop_reason

            turn = ObservedTurn(
                role=role,
                content=content,
                timestamp=timestamp,
                uuid=uuid,
                parent_uuid=parent_uuid,
                tool_calls=tool_blocks,
                metadata=turn_meta,
            )
            turns.append(turn)
            turn_counter[role] += 1
            chars, _ = measure_content(content)
            content_chars_total += chars

        if not turns:
            return None
        if turn_counter[ROLE_USER] == 0:
            return None

        metadata = self._build_session_metadata(
            lines=lines,
            source_path=fpath,
            turns=turns,
            user_turns=turn_counter[ROLE_USER],
            assistant_turns=turn_counter[ROLE_ASSISTANT],
            start_time=start_time,
            end_time=end_time,
            content_chars_total=content_chars_total,
            harness_line_types=harness_line_types,
            session_id_source=("filename_stem" if not session_id_from_field else None),
            session_title=(
                turns[0].content.split("\n")[0][:120] if turns[0].content else "Untitled"
            ),
        )

        file_meta = self._file_metadata.get(fpath, {})
        project = file_meta.get("project")

        return ObservedSession(
            session_id=session_id,
            source_path=fpath,
            start_time=start_time,
            end_time=end_time,
            project=project,
            parent_session_id=parent_session_id,
            turns=turns,
            metadata=metadata,
            is_subagent=is_subagent,
            agent_id=agent_id,
            agent_slug=agent_slug,
        )

    def _build_session_metadata(
        self,
        *,
        lines: list[dict[str, object]],
        source_path: Path,
        turns: list[ObservedTurn],
        user_turns: int,
        assistant_turns: int,
        start_time: datetime,
        end_time: datetime,
        content_chars_total: int,
        harness_line_types: dict[str, int],
        session_id_source: str | None,
        session_title: str,
    ) -> dict[str, object]:
        file_meta = self._file_metadata.get(source_path, {})
        metadata: dict[str, object] = {
            "store": file_meta.get("store", "unknown"),
            "project": file_meta.get("project"),
            "total_lines": len(lines),
            "turn_count": len(turns),
            "user_turns": user_turns,
            "assistant_turns": assistant_turns,
            "duration_minutes": round(max(0.0, (end_time - start_time).total_seconds() / 60.0), 1),
            "content_chars_total": content_chars_total,
            "session_title": session_title,
        }

        git_branch = self._first_string(lines, "gitBranch")
        if git_branch:
            metadata["git_branch"] = git_branch

        cwd = self._first_string(lines, "cwd")
        if cwd:
            metadata["cwd"] = cwd

        parent_session_id = self._first_string(lines, "parentSessionId")
        if parent_session_id:
            metadata["parent_session_id"] = parent_session_id

        agent_id = self._first_string(lines, "agentId")
        if agent_id:
            metadata["agent_id"] = agent_id

        agent_slug = self._first_string(lines, "agentSlug")
        if agent_slug:
            metadata["agent_slug"] = agent_slug

        tool_counts = self._tool_counts(lines)
        if tool_counts:
            metadata["tools_used"] = tool_counts
            metadata["tool_calls"] = sum(tool_counts.values())

        if harness_line_types:
            metadata["harness_line_types"] = harness_line_types

        if session_id_source:
            metadata["session_id_source"] = session_id_source

        return metadata

    @staticmethod
    def _make_title(text: str, summary: str | None = None) -> str:
        source = text.split("\n")[0].strip() if text else "Untitled"
        return source[:120] if source else "Untitled"

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
    def _first_string(lines: list[dict[str, object]], key: str) -> str | None:
        for line in lines:
            value = line.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _line_uuid(line: dict[str, object], idx: int) -> str:
        uuid = line.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
        return f"line-{idx}"

    @staticmethod
    def _line_parent_uuid(line: dict[str, object]) -> str | None:
        for key in ("parentUuid", "parent_uuid"):
            value = line.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _tool_counts(lines: list[dict[str, object]]) -> dict[str, int]:
        counts: Counter[str] = Counter()

        for line in lines:
            line_type = line.get("type")

            if line_type == "progress":
                data_obj = line.get("data")
                if isinstance(data_obj, dict):
                    data = cast(dict[str, object], data_obj)
                    tool_name = data.get("toolName")
                    if isinstance(tool_name, str) and tool_name:
                        counts[tool_name] += 1

            if line_type == "tool_use":
                tool_name = line.get("tool_name")
                if isinstance(tool_name, str) and tool_name:
                    counts[tool_name] += 1

        return dict(counts.most_common(25))
