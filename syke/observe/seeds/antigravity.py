from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

_DEFAULT_SOURCE_ROOTS = (
    Path("~/.antigravity").expanduser(),
    Path("~/.gemini/antigravity").expanduser(),
    Path("~/.gemini").expanduser(),
)
_TEXTUAL_SUFFIXES = {".json", ".jsonl", ".md", ".markdown", ".yaml", ".yml", ".txt"}
_NOISE_DIR_NAMES = {
    ".git",
    "cache",
    "caches",
    "extensions",
    "logs",
    "node_modules",
    "tmp",
    "temp",
    "antigravity-browser-profile",
}


class AntigravityObserveAdapter(ObserveAdapter):
    source = "antigravity"

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
        return sorted(discovered, key=lambda path: str(path))

    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]:
        explicit_paths = self._normalize_candidate_paths(paths)
        candidates = explicit_paths if explicit_paths is not None else self.discover()

        grouped: dict[Path, list[Path]] = {}
        for candidate in candidates:
            group_key = self._group_key(candidate)
            grouped.setdefault(group_key, []).append(candidate)

        for group_key in sorted(grouped, key=lambda path: str(path)):
            group_paths = sorted(grouped[group_key], key=lambda path: str(path))
            if explicit_paths is None and since:
                newest_mtime = max(self._mtime(path) for path in group_paths)
                if newest_mtime < since:
                    continue

            session = self._build_session(group_key, group_paths)
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
            return [resolved] if self._is_candidate_file(resolved) else []

        if not resolved.is_dir():
            return []

        results: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(resolved):
            dirnames[:] = [
                dirname for dirname in dirnames if dirname.lower() not in _NOISE_DIR_NAMES
            ]
            current_dir = Path(dirpath)
            if self._is_noise_path(current_dir):
                dirnames[:] = []
                continue
            for filename in filenames:
                path = current_dir / filename
                try:
                    child = path.resolve()
                except OSError:
                    continue
                if self._is_candidate_file(child):
                    results.append(child)
        return results

    def _is_candidate_file(self, path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() not in _TEXTUAL_SUFFIXES:
            return False
        if self._is_noise_path(path):
            return False
        return self._artifact_family(path) is not None

    def _build_session(self, group_key: Path, paths: list[Path]) -> ObservedSession | None:
        turns: list[ObservedTurn] = []
        project: str | None = None
        families: list[str] = []

        for path in paths:
            family = self._artifact_family(path)
            if family is None:
                continue
            families.append(family)
            artifact_turns, artifact_project = self._turns_from_artifact(path, family)
            if artifact_project and project is None:
                project = artifact_project
            turns.extend(artifact_turns)

        if not turns:
            return None

        turns.sort(key=lambda turn: (turn.timestamp, turn.role, turn.content))
        start_time = turns[0].timestamp
        end_time = turns[-1].timestamp
        source_path = paths[0]
        session_id = self._session_id(group_key, source_path)

        return ObservedSession(
            session_id=session_id,
            source_path=source_path,
            start_time=start_time,
            end_time=end_time,
            project=project,
            turns=turns,
            metadata={
                "artifact_family": "workflow_artifacts",
                "artifact_families": sorted(set(families)),
                "source_root": str(group_key),
                "artifact_count": len(paths),
            },
            source_instance_id=str(group_key),
        )

    def _turns_from_artifact(
        self,
        path: Path,
        family: str,
    ) -> tuple[list[ObservedTurn], str | None]:
        if path.suffix.lower() == ".jsonl":
            return self._turns_from_jsonl(path, family)
        if path.suffix.lower() == ".json":
            return self._turns_from_json(path, family)
        return self._turns_from_text(path, family)

    def _turns_from_jsonl(
        self,
        path: Path,
        family: str,
    ) -> tuple[list[ObservedTurn], str | None]:
        turns: list[ObservedTurn] = []
        project: str | None = None
        fallback_base = self._path_timestamp(path)

        for line_index, record in self._iter_jsonl(path):
            timestamp = self._record_timestamp(record) or (
                fallback_base + timedelta(microseconds=line_index)
            )
            content = self._artifact_content_from_json(record, family)
            if not content:
                continue
            if project is None:
                project = self._project_from_mapping(record)
            turns.append(
                ObservedTurn(
                    role=self._family_role(family),
                    content=content,
                    timestamp=timestamp,
                    metadata=self._compact_dict(
                        {
                            "artifact_family": family,
                            "artifact_path": str(path),
                            "source_line_index": line_index,
                            "source_event_type": self._as_str(record.get("type"))
                            or self._as_str(record.get("event")),
                        }
                    ),
                )
            )

        return turns, project

    def _turns_from_json(
        self,
        path: Path,
        family: str,
    ) -> tuple[list[ObservedTurn], str | None]:
        payload = self._load_json(path)
        if isinstance(payload, list):
            turns: list[ObservedTurn] = []
            project: str | None = None
            fallback_base = self._path_timestamp(path)
            for index, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                content = self._artifact_content_from_json(item, family)
                if not content:
                    continue
                if project is None:
                    project = self._project_from_mapping(item)
                turns.append(
                    ObservedTurn(
                        role=self._family_role(family),
                        content=content,
                        timestamp=self._record_timestamp(item)
                        or (fallback_base + timedelta(microseconds=index)),
                        metadata=self._compact_dict(
                            {
                                "artifact_family": family,
                                "artifact_path": str(path),
                                "source_index": index,
                            }
                        ),
                    )
                )
            return turns, project

        if not isinstance(payload, dict):
            return [], None

        content = self._artifact_content_from_json(payload, family)
        if not content:
            return [], None

        turn = ObservedTurn(
            role=self._family_role(family),
            content=content,
            timestamp=self._record_timestamp(payload) or self._path_timestamp(path),
            metadata=self._compact_dict(
                {
                    "artifact_family": family,
                    "artifact_path": str(path),
                    "status": self._as_str(payload.get("status")),
                }
            ),
        )
        return [turn], self._project_from_mapping(payload)

    def _turns_from_text(
        self,
        path: Path,
        family: str,
    ) -> tuple[list[ObservedTurn], str | None]:
        text = self._read_text(path)
        if not text:
            return [], None
        turn = ObservedTurn(
            role=self._family_role(family),
            content=text,
            timestamp=self._path_timestamp(path),
            metadata={
                "artifact_family": family,
                "artifact_path": str(path),
                "source_extension": path.suffix.lower(),
            },
        )
        return [turn], None

    def _group_key(self, path: Path) -> Path:
        parent = path.parent
        if parent == path:
            return path
        if (
            parent.name.lower()
            in {
                "artifacts",
                "artifact",
                "workflow",
                "workflows",
                "session",
                "sessions",
                "run",
                "runs",
                "task",
                "tasks",
            }
            and parent.parent != parent
        ):
            return parent.parent
        return parent

    def _artifact_family(self, path: Path) -> str | None:
        joined = "/".join(part.lower() for part in path.parts)
        name = path.stem.lower()
        if "task" in name or "/task" in joined:
            return "task"
        if (
            "implementation-plan" in name
            or "implementation_plan" in name
            or "implementationplan" in name
            or name == "plan"
        ):
            return "implementation_plan"
        if "walkthrough" in name:
            return "walkthrough"
        if "recording" in name or "browser" in name:
            return "browser_recording_metadata"
        if "verification" in name or "verify" in name or "screenshot" in name:
            return "screenshot_verification_metadata"
        return None

    def _family_role(self, family: str) -> str:
        return "user" if family == "task" else "assistant"

    def _artifact_content_from_json(self, value: dict[str, Any], family: str) -> str:
        pieces: list[str] = []
        for key in (
            "title",
            "name",
            "summary",
            "description",
            "task",
            "prompt",
            "goal",
            "plan",
            "walkthrough",
            "verdict",
            "result",
            "status",
            "notes",
            "content",
            "markdown",
            "text",
        ):
            item = value.get(key)
            text = self._content_to_text(item)
            if text:
                pieces.append(
                    f"{key}: {text}" if key not in {"content", "markdown", "text"} else text
                )
        if pieces:
            return "\n\n".join(dict.fromkeys(piece for piece in pieces if piece)).strip()
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return ""

    def _content_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("text", "value", "content", "summary", "description", "message"):
                text = self._content_to_text(value.get(key))
                if text:
                    return text
            return ""
        if isinstance(value, list):
            parts = [self._content_to_text(item) for item in value]
            return "\n\n".join(part for part in parts if part).strip()
        return ""

    def _project_from_mapping(self, value: dict[str, Any]) -> str | None:
        for key in ("project", "workspace", "cwd", "path", "root"):
            project = value.get(key)
            if isinstance(project, str) and project:
                return project
        return None

    def _record_timestamp(self, value: dict[str, Any]) -> datetime | None:
        for key in (
            "timestamp",
            "createdAt",
            "updatedAt",
            "completedAt",
            "recordedAt",
            "verifiedAt",
            "startTime",
            "endTime",
        ):
            parsed = self._parse_ts(value.get(key))
            if parsed is not None:
                return parsed
        return None

    def _session_id(self, group_key: Path, source_path: Path) -> str:
        root_names = {root.name for root in self._source_roots()}
        if group_key.name and group_key.name not in root_names:
            return group_key.name
        return source_path.stem

    def _is_noise_path(self, path: Path) -> bool:
        return any(part.lower() in _NOISE_DIR_NAMES for part in path.parts)

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

    def _read_text(self, path: Path) -> str:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _mtime(self, path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _path_timestamp(self, path: Path) -> datetime:
        return datetime.fromtimestamp(self._mtime(path), tz=UTC)

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

    @staticmethod
    def _as_str(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
        return {key: item for key, item in value.items() if item is not None}
