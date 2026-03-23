"""DynamicAdapter — loads a generated parse_line() from disk, wraps it as ObserveAdapter."""

from __future__ import annotations

import importlib.util
import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from syke.db import SykeDB
from syke.observe.observe import ObserveAdapter, ObservedSession, ObservedTurn

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
    ):
        super().__init__(db, user_id)
        self.source = source_name
        self._adapter_dir = adapter_dir
        self._discover_roots = discover_roots or []
        self._file_glob = file_glob

        adapter_py = adapter_dir / "adapter.py"
        self._module = _load_parse_line(adapter_py)
        self._parse_line = self._module.parse_line

    def discover(self) -> list[Path]:
        paths: list[Path] = []
        for root in self._discover_roots:
            root = root.expanduser()
            if not root.exists():
                continue
            if root.is_file():
                paths.append(root)
                continue
            for match in root.glob(self._file_glob):
                if match.is_file():
                    paths.append(match)
        return sorted(set(paths))

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        for fpath in self.discover():
            sessions = self._parse_file(fpath)
            yield from sessions.values()

    def _parse_file(self, fpath: Path) -> dict[str, ObservedSession]:
        sessions: dict[str, ObservedSession] = {}
        fallback_session_id = fpath.stem

        try:
            with fpath.open("r", encoding="utf-8", errors="replace") as f:
                for line_idx, raw_line in enumerate(f):
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
                        turn = ObservedTurn(
                            role=role,
                            content=content,
                            timestamp=ts,
                            tool_calls=tool_blocks,
                            metadata={
                                k: v
                                for k, v in parsed.items()
                                if k
                                not in {
                                    "session_id",
                                    "timestamp",
                                    "role",
                                    "content",
                                    "event_type",
                                    "parent_session_id",
                                    "tool_name",
                                }
                                and v is not None
                            },
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
