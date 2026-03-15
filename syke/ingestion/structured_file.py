from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast, override

from syke.config_file import expand_path
from syke.db import SykeDB
from syke.ingestion import parsers
from syke.ingestion.descriptor import HarnessDescriptor
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn

logger = logging.getLogger(__name__)

PARSER_REGISTRY: dict[str, Callable[..., object]] = {
    "extract_text_content": parsers.extract_text_content,
    "extract_tool_blocks": parsers.extract_tool_blocks,
    "read_jsonl": parsers.read_jsonl,
    "parse_timestamp": parsers.parse_timestamp,
    "read_json": parsers.read_json,
    "extract_field": parsers.extract_field,
    "normalize_role": parsers.normalize_role,
}


def _get_parser(name: str) -> Callable[..., object]:
    func = PARSER_REGISTRY.get(name)
    if func is None:
        raise ValueError(f"Unknown parser: {name}")
    return func


class StructuredFileAdapter(ObserveAdapter):
    def __init__(self, db: SykeDB, user_id: str, descriptor: HarnessDescriptor):
        self.descriptor = descriptor
        self.source = descriptor.source
        super().__init__(db, user_id)

    @override
    def discover(self) -> list[Path]:
        discover_cfg = self.descriptor.discover
        if discover_cfg is None:
            return []

        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        since_epoch = (
            datetime.fromisoformat(last_sync).replace(tzinfo=UTC).timestamp() if last_sync else 0.0
        )

        candidates: list[tuple[int, float, Path]] = []
        for root in discover_cfg.roots:
            root_path = expand_path(root.path)
            if not root_path.exists():
                continue

            for pattern in root.include:
                for path in root_path.glob(pattern):
                    if not path.is_file():
                        continue
                    mtime = path.stat().st_mtime
                    if mtime < since_epoch:
                        continue
                    candidates.append((root.priority, mtime, path))

        candidates.sort(key=lambda row: (-row[0], -row[1], str(row[2])))

        discovered: list[Path] = []
        seen_stems: set[str] = set()
        for _, _, path in candidates:
            if path.stem in seen_stems:
                continue
            seen_stems.add(path.stem)
            discovered.append(path)

        return discovered

    @override
    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        for fpath in self.discover():
            if since and fpath.stat().st_mtime < since:
                continue

            try:
                session = self._parse_session(fpath)
            except Exception as exc:
                logger.warning("Failed to parse session %s: %s", fpath.name, exc)
                continue

            if session is not None:
                yield session

    def _parse_session(self, fpath: Path) -> ObservedSession | None:
        lines = self._read_lines(fpath)
        if not lines:
            return None

        session_cfg = self.descriptor.session
        turn_cfg = self.descriptor.turn
        if session_cfg is None or turn_cfg is None:
            return None

        session_id = self._extract_session_id(lines, fpath)
        if session_id is None:
            return None

        start_time = self._extract_start_time(lines)
        if start_time is None:
            return None

        parent_session_id = self._extract_first_string(
            lines,
            session_cfg.parent_session_id.field if session_cfg.parent_session_id else None,
        )

        turns = self._extract_turns(lines, start_time, session_id)
        if not turns:
            return None

        metadata = self._extract_metadata(lines, fpath)
        if parent_session_id:
            metadata.setdefault("parent_session_id", parent_session_id)

        return ObservedSession(
            session_id=session_id,
            source_path=fpath,
            start_time=start_time,
            end_time=turns[-1].timestamp,
            parent_session_id=parent_session_id,
            turns=turns,
            metadata=metadata,
        )

    def _read_lines(self, fpath: Path) -> list[dict[str, object]]:
        if self.descriptor.format_cluster == "jsonl":
            reader = cast(Callable[[Path], list[dict[str, object]]], _get_parser("read_jsonl"))
            return reader(fpath)

        if self.descriptor.format_cluster == "json":
            reader = cast(Callable[[Path], object], _get_parser("read_json"))
            parsed = reader(fpath)
            if parsed is None:
                return []
            if isinstance(parsed, list):
                return [
                    cast(dict[str, object], item) for item in parsed if isinstance(item, Mapping)
                ]
            if isinstance(parsed, Mapping):
                return [cast(dict[str, object], parsed)]
            return []

        raise ValueError(f"Unsupported format cluster: {self.descriptor.format_cluster}")

    def _extract_session_id(self, lines: list[dict[str, object]], fpath: Path) -> str | None:
        session_cfg = self.descriptor.session
        if session_cfg is None:
            return None

        field = session_cfg.id_field
        if field:
            for line in lines:
                value = self._extract_field(line, field)
                if isinstance(value, str) and value:
                    return value

        fallback = session_cfg.id_fallback
        if fallback == "$file.stem":
            return fpath.stem
        if isinstance(fallback, str) and fallback:
            return fallback
        return None

    def _extract_start_time(self, lines: list[dict[str, object]]) -> datetime | None:
        session_cfg = self.descriptor.session
        if session_cfg is None or session_cfg.start_time is None:
            return None

        timestamp_parser = cast(
            Callable[[dict[str, object]], datetime | None], _get_parser("parse_timestamp")
        )
        path = session_cfg.start_time.first_timestamp

        for line in lines:
            value = self._extract_field(line, path)
            ts = timestamp_parser({"timestamp": cast(object, value)})
            if ts is not None:
                return ts

        return None

    def _extract_turns(
        self,
        lines: list[dict[str, object]],
        start_time: datetime,
        session_id: str,
    ) -> list[ObservedTurn]:
        turn_cfg = self.descriptor.turn
        if turn_cfg is None:
            return []

        content_parser = cast(
            Callable[[dict[str, object]], object], _get_parser(turn_cfg.content_parser)
        )
        tool_parser: Callable[[dict[str, object]], object] | None = None
        if turn_cfg.tool_parser:
            tool_parser = cast(
                Callable[[dict[str, object]], object], _get_parser(turn_cfg.tool_parser)
            )

        timestamp_parser = cast(
            Callable[[dict[str, object]], datetime | None], _get_parser("parse_timestamp")
        )
        normalize_role = cast(Callable[[str], str], _get_parser("normalize_role"))

        turns: list[ObservedTurn] = []
        for idx, line in enumerate(lines):
            if not self._is_turn_match(line):
                continue

            raw_role = self._extract_field(line, turn_cfg.role_field)
            if not isinstance(raw_role, str) or not raw_role:
                continue

            role = normalize_role(raw_role)
            content_raw = content_parser(line)
            content = content_raw if isinstance(content_raw, str) else ""

            tool_blocks: list[dict[str, Any]] = []
            if tool_parser is not None:
                parsed_tool_blocks = tool_parser(line)
                if isinstance(parsed_tool_blocks, list):
                    tool_blocks = [
                        cast(dict[str, Any], item)
                        for item in parsed_tool_blocks
                        if isinstance(item, Mapping)
                    ]

            if not content and not tool_blocks:
                continue

            ts_value = self._extract_field(line, turn_cfg.timestamp_field)
            parsed_ts = timestamp_parser({"timestamp": cast(object, ts_value)})
            timestamp = parsed_ts or start_time

            metadata: dict[str, object] = {
                "source_line_index": idx,
                "source_event_type": raw_role,
            }
            if parsed_ts is None:
                metadata["timestamp_inferred"] = True

            if self.descriptor.external_id is not None:
                metadata["external_id"] = self.descriptor.expand_external_id(
                    session_id=session_id,
                    sequence_index=len(turns),
                )

            turns.append(
                ObservedTurn(
                    role=role,
                    content=content,
                    timestamp=timestamp,
                    tool_calls=tool_blocks,
                    metadata=metadata,
                )
            )

        return turns

    def _extract_metadata(self, lines: list[dict[str, object]], fpath: Path) -> dict[str, object]:
        metadata: dict[str, object] = {}
        for field in self.descriptor.metadata.fields:
            value: object | None = None

            if field.path:
                value = self._first_non_none(lines, field.path)

            if value is None and field.first:
                value = self._extract_first_string(lines, field.first)

            if value is None and field.parser:
                parser_func = _get_parser(field.parser)
                try:
                    value = parser_func(lines)
                except TypeError:
                    try:
                        value = parser_func(lines[0])
                    except TypeError:
                        value = parser_func(fpath)
                except Exception as exc:
                    logger.warning(
                        "Metadata parser '%s' failed for %s: %s",
                        field.parser,
                        fpath.name,
                        exc,
                    )
                    value = None

            if value is not None:
                metadata[field.key] = value

        return metadata

    def _is_turn_match(self, line: dict[str, object]) -> bool:
        turn_cfg = self.descriptor.turn
        if turn_cfg is None or turn_cfg.match is None:
            return True

        value = self._extract_field(line, turn_cfg.match.field)
        return isinstance(value, str) and value in turn_cfg.match.values

    @staticmethod
    def _extract_field(obj: Mapping[str, object], dotted_path: str) -> object | None:
        parser = cast(
            Callable[[Mapping[str, object], str], object | None], _get_parser("extract_field")
        )
        return parser(obj, dotted_path)

    def _first_non_none(self, lines: list[dict[str, object]], dotted_path: str) -> object | None:
        for line in lines:
            value = self._extract_field(line, dotted_path)
            if value is not None:
                return value
        return None

    def _extract_first_string(
        self, lines: list[dict[str, object]], dotted_path: str | None
    ) -> str | None:
        if not dotted_path:
            return None
        for line in lines:
            value = self._extract_field(line, dotted_path)
            if isinstance(value, str) and value.strip():
                return value
        return None
