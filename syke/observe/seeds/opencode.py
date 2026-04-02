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
    spec = get_source("opencode")
    if spec is None:
        return ()
    return tuple(discovered_roots(spec))


_DB_FILENAME_RE = re.compile(r"^opencode(?:-[^.]+)?\.db$")
_SUBAGENT_TITLE_RE = re.compile(r"\(@([^\s)]+)\s+subagent\)", re.IGNORECASE)


class OpencodeObserveAdapter(ObserveAdapter):
    source = "opencode"

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
        return sorted(discovered, key=lambda path: str(path))

    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]:
        explicit_paths = self._normalize_candidate_paths(paths)
        candidates = explicit_paths if explicit_paths is not None else self.discover()

        for db_path in sorted(candidates, key=lambda candidate: str(candidate)):
            if explicit_paths is None and since:
                try:
                    if db_path.stat().st_mtime < since:
                        continue
                except OSError:
                    continue

            for session in self._iter_sessions_from_db(
                db_path,
                since=since if explicit_paths is None else 0,
            ):
                if session.turns:
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
            return [resolved] if self._is_session_db_file(resolved) else []

        if not resolved.is_dir():
            return []

        matches: list[Path] = []
        for child in sorted(resolved.glob("opencode*.db"), key=lambda path: path.name):
            if not self._is_session_db_file(child):
                continue
            try:
                matches.append(child.resolve())
            except OSError:
                continue
        return matches

    def _is_session_db_file(self, path: Path) -> bool:
        return path.is_file() and bool(_DB_FILENAME_RE.fullmatch(path.name))

    def _iter_sessions_from_db(self, db_path: Path, since: float = 0) -> Iterable[ObservedSession]:
        conn = self._connect_readonly(db_path)
        if conn is None:
            return

        try:
            for row in self._session_rows(conn, since):
                session = self._build_session(conn, db_path, row)
                if session is not None:
                    yield session
        finally:
            conn.close()

    def _session_rows(self, conn: sqlite3.Connection, since: float) -> Iterable[sqlite3.Row]:
        since_ms = int(since * 1000) if since else 0
        where = ""
        params: tuple[Any, ...] = ()
        if since_ms:
            where = """
            WHERE s.time_updated >= ?
               OR s.time_created >= ?
               OR COALESCE(s.time_archived, 0) >= ?
               OR EXISTS (
                    SELECT 1
                    FROM message m
                    WHERE m.session_id = s.id
                      AND (m.time_updated >= ? OR m.time_created >= ?)
               )
               OR EXISTS (
                    SELECT 1
                    FROM part p
                    WHERE p.session_id = s.id
                      AND (p.time_updated >= ? OR p.time_created >= ?)
               )
            """
            params = (since_ms, since_ms, since_ms, since_ms, since_ms, since_ms, since_ms)

        query = f"""
        SELECT
            s.id,
            s.project_id,
            s.parent_id,
            s.slug,
            s.directory,
            s.title,
            s.version,
            s.share_url,
            s.summary_additions,
            s.summary_deletions,
            s.summary_files,
            s.summary_diffs,
            s.revert,
            s.permission,
            s.time_created,
            s.time_updated,
            s.time_compacting,
            s.time_archived,
            s.workspace_id,
            p.worktree AS project_worktree,
            p.vcs AS project_vcs,
            p.name AS project_name,
            p.commands AS project_commands,
            p.time_created AS project_time_created,
            p.time_updated AS project_time_updated,
            w.type AS workspace_type,
            w.name AS workspace_name,
            w.directory AS workspace_directory,
            w.extra AS workspace_extra,
            ss.id AS share_id,
            ss.secret AS share_secret,
            ss.url AS share_record_url
        FROM session s
        LEFT JOIN project p ON p.id = s.project_id
        LEFT JOIN workspace w ON w.id = s.workspace_id
        LEFT JOIN session_share ss ON ss.session_id = s.id
        {where}
        ORDER BY s.time_created ASC, s.id ASC
        """
        yield from conn.execute(query, params)

    def _build_session(
        self,
        conn: sqlite3.Connection,
        db_path: Path,
        row: sqlite3.Row,
    ) -> ObservedSession | None:
        session_id = self._as_str(row["id"])
        if not session_id:
            return None

        messages = list(
            conn.execute(
                """
                SELECT id, session_id, time_created, time_updated, data
                FROM message
                WHERE session_id = ?
                ORDER BY time_created ASC, id ASC
                """,
                (session_id,),
            )
        )
        if not messages:
            return None

        parts_by_message: dict[str, list[sqlite3.Row]] = {}
        for part_row in conn.execute(
            """
            SELECT id, message_id, session_id, time_created, time_updated, data
            FROM part
            WHERE session_id = ?
            ORDER BY time_created ASC, id ASC
            """,
            (session_id,),
        ):
            message_id = self._as_str(part_row["message_id"])
            if not message_id:
                continue
            parts_by_message.setdefault(message_id, []).append(part_row)

        turns: list[ObservedTurn] = []
        start_time = self._parse_ts(row["time_created"])
        end_time = self._max_dt(
            self._parse_ts(row["time_updated"]),
            self._parse_ts(row["time_archived"]),
        )
        agent_id = self._infer_agent_id(row, messages)
        agent_slug = self._slugify(agent_id) if agent_id else None

        for message_row in messages:
            payload = self._loads_json(message_row["data"])
            if not isinstance(payload, dict):
                continue

            role = self._as_str(payload.get("role"))
            if role not in {"user", "assistant"}:
                continue

            timestamp = self._message_timestamp(payload, message_row)
            start_time = timestamp if timestamp < start_time else start_time
            end_time = self._max_dt(end_time, timestamp)

            turn = self._message_to_turn(
                row=row,
                message_row=message_row,
                message=payload,
                parts=parts_by_message.get(message_row["id"], []),
                role=role,
                timestamp=timestamp,
            )
            if turn is None:
                continue
            turns.append(turn)

        if not turns:
            return None

        project = self._as_str(row["directory"]) or self._as_str(row["project_worktree"])
        metadata = self._compact_dict(
            {
                "artifact_family": "sqlite",
                "db_path": self._clean_string(str(db_path)),
                "project_id": self._as_str(row["project_id"]),
                "project_worktree": self._as_str(row["project_worktree"]),
                "project_vcs": self._as_str(row["project_vcs"]),
                "project_name": self._as_str(row["project_name"]),
                "project_commands": self._loads_json(row["project_commands"]),
                "project_time_created": self._ts_iso(row["project_time_created"]),
                "project_time_updated": self._ts_iso(row["project_time_updated"]),
                "slug": self._as_str(row["slug"]),
                "title": self._as_str(row["title"]),
                "version": self._as_str(row["version"]),
                "share_url": self._as_str(row["share_url"]),
                "summary_additions": self._as_int(row["summary_additions"]),
                "summary_deletions": self._as_int(row["summary_deletions"]),
                "summary_files": self._as_int(row["summary_files"]),
                "summary_diffs": self._loads_json(row["summary_diffs"]),
                "revert": self._loads_json(row["revert"]),
                "permission": self._loads_json(row["permission"]),
                "time_compacting": self._ts_iso(row["time_compacting"]),
                "time_archived": self._ts_iso(row["time_archived"]),
                "workspace_id": self._as_str(row["workspace_id"]),
                "workspace_type": self._as_str(row["workspace_type"]),
                "workspace_name": self._as_str(row["workspace_name"]),
                "workspace_directory": self._as_str(row["workspace_directory"]),
                "workspace_extra": self._loads_json(row["workspace_extra"]),
                "share_record_id": self._as_str(row["share_id"]),
                "share_record_secret": self._as_str(row["share_secret"]),
                "share_record_url": self._as_str(row["share_record_url"]),
            }
        )

        return ObservedSession(
            session_id=session_id,
            source_path=db_path,
            start_time=start_time,
            end_time=end_time,
            project=project,
            parent_session_id=self._as_str(row["parent_id"]),
            turns=turns,
            metadata=metadata,
            is_subagent=self._as_str(row["parent_id"]) is not None,
            agent_id=agent_id,
            agent_slug=agent_slug,
            source_instance_id=self._clean_string(f"{db_path}#{session_id}"),
        )

    def _message_to_turn(
        self,
        row: sqlite3.Row,
        message_row: sqlite3.Row,
        message: dict[str, Any],
        parts: list[sqlite3.Row],
        role: str,
        timestamp: datetime,
    ) -> ObservedTurn | None:
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        part_types: list[str] = []
        finish_reason: str | None = None
        file_count = 0
        patch_count = 0
        reasoning_count = 0

        for part_row in parts:
            payload = self._loads_json(part_row["data"])
            if not isinstance(payload, dict):
                continue

            part_type = self._as_str(payload.get("type")) or "unknown"
            part_types.append(part_type)

            if part_type == "text":
                text = self._as_str(payload.get("text"))
                if text:
                    content_parts.append(text)
                continue

            if part_type == "reasoning":
                text = self._as_str(payload.get("text"))
                if text:
                    content_parts.append(f"[reasoning]\n{text}")
                    reasoning_count += 1
                continue

            if part_type == "tool":
                tool_name = self._as_str(payload.get("tool"))
                state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
                call_id = self._as_str(payload.get("callID"))
                tool_calls.append(
                    {
                        "block_type": "tool_use",
                        "tool_name": tool_name,
                        "tool_id": call_id,
                        "input": state.get("input") if isinstance(state.get("input"), dict) else {},
                    }
                )
                status = self._as_str(state.get("status"))
                if status in {"completed", "error"}:
                    result_content = state.get("output")
                    if status == "error" and result_content is None:
                        result_content = state.get("error")
                    tool_calls.append(
                        {
                            "block_type": "tool_result",
                            "tool_use_id": call_id,
                            "content": self._stringify_tool_content(result_content),
                            "is_error": status == "error",
                        }
                    )
                continue

            if part_type == "patch":
                patch_count += 1
                content_parts.append(self._summarize_patch(payload))
                continue

            if part_type == "file":
                file_count += 1
                content_parts.append(self._summarize_file(payload))
                continue

            if part_type == "compaction":
                content_parts.append(self._summarize_compaction(payload))
                continue

            if part_type == "step-finish":
                finish_reason = self._as_str(payload.get("reason")) or finish_reason
                continue

        model, provider = self._message_model(message)
        usage = self._message_usage(message)
        error = message.get("error") if isinstance(message.get("error"), dict) else None
        metadata = self._compact_dict(
            {
                "source_message_id": self._as_str(message_row["id"]),
                "source_parent_message_id": self._as_str(message.get("parentID")),
                "source_event_type": role,
                "source_line_index": self._as_int(message_row["time_created"]),
                "model": model,
                "provider": provider,
                "mode": self._as_str(message.get("mode")),
                "agent": self._as_str(message.get("agent")),
                "finish": self._as_str(message.get("finish")) or finish_reason,
                "stop_reason": self._as_str(message.get("finish")) or finish_reason,
                "usage": usage or None,
                "path": message.get("path") if isinstance(message.get("path"), dict) else None,
                "summary": (
                    message.get("summary") if isinstance(message.get("summary"), dict) else None
                ),
                "tools": message.get("tools") if isinstance(message.get("tools"), dict) else None,
                "error": error,
                "part_types": part_types or None,
                "file_count": file_count or None,
                "patch_count": patch_count or None,
                "reasoning_count": reasoning_count or None,
                "session_title": self._as_str(row["title"]),
                "session_slug": self._as_str(row["slug"]),
            }
        )

        content = self._join_content(content_parts)
        if not content and not tool_calls and error:
            content = self._stringify_tool_content(error)
        if not content and not tool_calls:
            return None

        return ObservedTurn(
            role=role,
            content=content,
            timestamp=timestamp,
            uuid=self._as_str(message_row["id"]),
            parent_uuid=self._as_str(message.get("parentID")),
            tool_calls=tool_calls,
            metadata=metadata,
        )

    def _infer_agent_id(self, row: sqlite3.Row, messages: list[sqlite3.Row]) -> str | None:
        title = self._as_str(row["title"])
        if title:
            match = _SUBAGENT_TITLE_RE.search(title)
            if match:
                return match.group(1)

        if self._as_str(row["parent_id"]):
            for message_row in messages:
                payload = self._loads_json(message_row["data"])
                if not isinstance(payload, dict):
                    continue
                agent = self._as_str(payload.get("agent"))
                if agent:
                    return agent
        return None

    def _message_timestamp(self, message: dict[str, Any], message_row: sqlite3.Row) -> datetime:
        time_obj = message.get("time") if isinstance(message.get("time"), dict) else {}
        return (
            self._parse_ts(time_obj.get("created"))
            or self._parse_ts(message_row["time_created"])
            or self._parse_ts(message_row["time_updated"])
            or datetime.fromtimestamp(0, tz=UTC)
        )

    def _message_model(self, message: dict[str, Any]) -> tuple[str | None, str | None]:
        model = self._as_str(message.get("modelID"))
        provider = self._as_str(message.get("providerID"))
        model_obj = message.get("model") if isinstance(message.get("model"), dict) else {}
        return model or self._as_str(model_obj.get("modelID")), provider or self._as_str(
            model_obj.get("providerID")
        )

    def _message_usage(self, message: dict[str, Any]) -> dict[str, Any]:
        tokens = message.get("tokens") if isinstance(message.get("tokens"), dict) else {}
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        usage = {
            "input_tokens": self._as_int(tokens.get("input")),
            "output_tokens": self._as_int(tokens.get("output")),
            "reasoning_tokens": self._as_int(tokens.get("reasoning")),
            "cache_read_input_tokens": self._as_int(cache.get("read")),
            "cache_creation_input_tokens": self._as_int(cache.get("write")),
            "total_tokens": self._as_int(tokens.get("total")),
            "cost": message.get("cost"),
        }
        return self._compact_dict(usage)

    @staticmethod
    def _connect_readonly(db_path: Path) -> sqlite3.Connection | None:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return None
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _join_content(parts: list[str]) -> str:
        cleaned = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
        return "\n\n".join(cleaned)

    @staticmethod
    def _summarize_patch(payload: dict[str, Any]) -> str:
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        listed = [str(item) for item in files[:10] if item is not None]
        suffix = f": {', '.join(listed)}" if listed else ""
        return f"[patch] {payload.get('hash', '')}{suffix}".strip()

    @staticmethod
    def _summarize_file(payload: dict[str, Any]) -> str:
        filename = payload.get("filename")
        mime = payload.get("mime")
        source = payload.get("source")
        parts = ["[file]"]
        if isinstance(filename, str) and filename:
            parts.append(filename)
        if isinstance(mime, str) and mime:
            parts.append(f"({mime})")
        if isinstance(source, str) and source:
            parts.append(f"source={source}")
        return " ".join(parts)

    @staticmethod
    def _summarize_compaction(payload: dict[str, Any]) -> str:
        auto = payload.get("auto")
        return f"[compaction] auto={bool(auto)}"

    @staticmethod
    def _stringify_tool_content(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return OpencodeObserveAdapter._clean_string(value)
        return json.dumps(
            OpencodeObserveAdapter._clean_value(value),
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _loads_json(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return None
        try:
            return OpencodeObserveAdapter._clean_value(json.loads(value))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _as_str(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        cleaned = OpencodeObserveAdapter._clean_string(value)
        return cleaned or None

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
    def _slugify(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")

    @staticmethod
    def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: OpencodeObserveAdapter._clean_value(item)
            for key, item in value.items()
            if item is not None
        }

    @staticmethod
    def _clean_string(value: str) -> str:
        return value.encode("utf-8", errors="replace").decode("utf-8")

    @staticmethod
    def _clean_value(value: Any) -> Any:
        if isinstance(value, str):
            return OpencodeObserveAdapter._clean_string(value)
        if isinstance(value, dict):
            return {
                OpencodeObserveAdapter._clean_string(str(key)): (
                    OpencodeObserveAdapter._clean_value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [OpencodeObserveAdapter._clean_value(item) for item in value]
        if isinstance(value, tuple):
            return [OpencodeObserveAdapter._clean_value(item) for item in value]
        return value

    @staticmethod
    def _parse_ts(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
            except ValueError:
                return None
        if isinstance(value, int | float):
            ts = float(value)
            if ts <= 0:
                return None
            if ts > 1e18:
                ts /= 1e9
            elif ts > 1e15:
                ts /= 1e6
            elif ts > 1e12:
                ts /= 1e3
            return datetime.fromtimestamp(ts, tz=UTC)
        return None

    @classmethod
    def _ts_iso(cls, value: Any) -> str | None:
        dt = cls._parse_ts(value)
        return dt.isoformat() if dt is not None else None

    @staticmethod
    def _max_dt(*values: datetime | None) -> datetime | None:
        present = [value for value in values if value is not None]
        return max(present) if present else None
