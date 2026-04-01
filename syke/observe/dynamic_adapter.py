"""DynamicAdapter — loads a generated parse_line() from disk, wraps it as ObserveAdapter."""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

logger = logging.getLogger(__name__)


def _load_parse_line(adapter_py: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"syke_dynamic_{adapter_py.parent.name}",
        adapter_py,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {adapter_py}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "parse_line"):
        raise ImportError(f"Module {adapter_py} has no parse_line() function")
    return module


class DynamicAdapter(ObserveAdapter):
    """ObserveAdapter backed by a generated parse_line() loaded from disk.

    The adapter reads files from discover paths defined in descriptor.toml,
    calls parse_line() on each line, and groups results into ObservedSession
    objects for the standard ingest() pipeline.
    """

    def __init__(
        self,
        db: SykeDB,
        user_id: str,
        source_name: str,
        adapter_dir: Path,
        discover_roots: list[Path] | None = None,
        file_glob: str = "**/*.jsonl",
        discover_specs: list[tuple[Path, tuple[str, ...]]] | None = None,
    ):
        super().__init__(db, user_id)
        self.source = source_name
        self._adapter_dir = adapter_dir
        self._discover_specs = discover_specs or [
            (root, (file_glob,)) for root in (discover_roots or [])
        ]

        adapter_py = adapter_dir / "adapter.py"
        self._module = _load_parse_line(adapter_py)
        self._parse_line = self._module.parse_line

    def discover(self) -> list[Path]:
        paths: list[Path] = []
        for root, patterns in self._discover_specs:
            root = root.expanduser()
            if not root.exists():
                continue
            if root.is_file():
                paths.append(root)
                continue
            for pattern in patterns:
                for match in root.glob(pattern):
                    if match.is_file():
                        paths.append(match)
        return sorted(set(paths))

    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]:
        explicit_paths = self._normalize_candidate_paths(paths)
        for fpath in explicit_paths or self.discover():
            # When reconcile scopes us to explicit dirty paths from the watcher,
            # trust that file list and bypass the coarse mtime cursor filter.
            if explicit_paths is None and since:
                try:
                    if fpath.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
            sessions = self._parse_file(fpath)
            yield from sessions.values()

    def _normalize_candidate_paths(self, paths: Iterable[Path] | None) -> list[Path] | None:
        if paths is None:
            return None

        normalized: list[Path] = []
        for candidate in paths:
            path = Path(candidate).expanduser()
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not resolved.is_file():
                continue
            if not self._matches_discover_root(resolved):
                continue
            normalized.append(resolved)
        return sorted(set(normalized))

    def _matches_discover_root(self, path: Path) -> bool:
        for root, _patterns in self._discover_specs:
            root = root.expanduser()
            try:
                resolved_root = root.resolve()
            except OSError:
                continue
            if resolved_root.is_file():
                if path == resolved_root:
                    return True
                continue
            try:
                path.relative_to(resolved_root)
                return True
            except ValueError:
                continue
        return False

    def _parse_file(self, fpath: Path) -> dict[str, ObservedSession]:
        sessions: dict[str, ObservedSession] = {}
        fallback_session_id = fpath.stem

        try:
            with fpath.open("r", encoding="utf-8", errors="replace") as f:
                for _line_idx, raw_line in enumerate(f):
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        parsed = self._parse_line(raw_line)
                    except Exception:
                        continue
                    if parsed is None:
                        continue

                    session_id = str(parsed.get("session_id") or fallback_session_id)
                    ts = self._parse_ts(parsed.get("timestamp"))
                    role = str(parsed.get("role", "assistant"))
                    content = str(parsed.get("content", ""))
                    event_type = parsed.get("event_type", "turn")

                    if session_id not in sessions:
                        sessions[session_id] = ObservedSession(
                            session_id=session_id,
                            source_path=fpath,
                            start_time=ts,
                            parent_session_id=parsed.get("parent_session_id"),
                        )

                    session = sessions[session_id]
                    if ts > (session.end_time or session.start_time):
                        session.end_time = ts

                    if event_type in ("turn", "session.start"):
                        tool_blocks: list[dict] = []
                        # Build metadata, nesting token fields under "usage"
                        # so session_to_events can find them
                        _skip = {
                            "session_id",
                            "timestamp",
                            "role",
                            "content",
                            "event_type",
                            "parent_session_id",
                            "tool_name",
                            "input_tokens",
                            "output_tokens",
                        }
                        meta = {k: v for k, v in parsed.items() if k not in _skip and v is not None}
                        # Nest tokens under usage so session_to_events can find
                        # metadata.usage.input_tokens.
                        in_tok = parsed.get("input_tokens")
                        out_tok = parsed.get("output_tokens")
                        if in_tok is not None or out_tok is not None:
                            meta["usage"] = {
                                "input_tokens": in_tok,
                                "output_tokens": out_tok,
                            }
                        turn = ObservedTurn(
                            role=role,
                            content=content,
                            timestamp=ts,
                            tool_calls=tool_blocks,
                            metadata=meta,
                        )
                        session.turns.append(turn)
                    elif event_type in ("tool_call", "tool_result"):
                        tool_block = {
                            "block_type": "tool_use"
                            if event_type == "tool_call"
                            else "tool_result",
                            "tool_name": parsed.get("tool_name"),
                            "input": parsed.get("content", ""),
                        }
                        if session.turns:
                            session.turns[-1].tool_calls.append(tool_block)

        except OSError:
            logger.warning("Cannot read %s", fpath, exc_info=True)

        return sessions

    @staticmethod
    def _parse_ts(raw: object) -> datetime:
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(raw, tz=UTC)
            except (OSError, ValueError):
                pass
        return datetime.now(UTC)
