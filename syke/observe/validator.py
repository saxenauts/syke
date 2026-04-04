from __future__ import annotations

import itertools
import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.observe.adapter import ObserveAdapter
from syke.observe.registry import _load_adapter_class

_MAX_SESSION_SAMPLE = 32


@dataclass(frozen=True)
class ValidationResult:
    source: str
    ok: bool
    checked_at: str
    adapter_hash: str
    summary: str
    details: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


class _FakeDB:
    db_path = ":memory:"

    def event_exists_by_external_id(self, *args, **kwargs):
        return False

    def insert_event(self, *args, **kwargs):
        return True

    def transaction(self):
        from contextlib import nullcontext

        return nullcontext()

    def start_ingestion_run(self, *args, **kwargs):
        return "validate"

    def complete_ingestion_run(self, *args, **kwargs):
        return None


def validate_adapter(source: str, adapter_path: Path, source_paths: list[Path]) -> ValidationResult:
    checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    adapter_hash = _file_hash(adapter_path)
    adapter_cls = _load_adapter_class(adapter_path, source)
    if adapter_cls is None:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary="adapter does not define an ObserveAdapter subclass",
            details={},
        )

    try:
        adapter = _instantiate_adapter(adapter_cls, source_paths)
    except Exception as exc:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary=f"adapter failed to initialize ({exc})",
            details={},
        )

    scoped_paths = _validation_scope(source_paths)
    if not scoped_paths:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary="no source artifacts found for validation",
            details={},
        )

    probe_path = scoped_paths[0].with_name(f"__syke_missing_scope__{scoped_paths[0].suffix}")
    try:
        if list(adapter.iter_sessions(since=0, paths=[probe_path])):
            return ValidationResult(
                source=source,
                ok=False,
                checked_at=checked_at,
                adapter_hash=adapter_hash,
                summary="adapter ignored explicit path scope",
                details={},
            )
    except Exception as exc:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary=f"path-scope probe failed ({exc})",
            details={},
        )

    try:
        sessions = _sample_sessions(adapter, scoped_paths)
    except Exception as exc:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary=f"adapter failed on real source data ({exc})",
            details={},
        )

    if not sessions:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary="adapter produced no sessions",
            details={},
        )

    session_error = _validate_sessions(adapter, sessions)
    if session_error is not None:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary=session_error,
            details={"sessions": len(sessions), "scoped_paths": len(scoped_paths)},
        )

    ingest_error = _validate_ingest_stability(adapter_cls, source, scoped_paths, sessions)
    if ingest_error is not None:
        return ValidationResult(
            source=source,
            ok=False,
            checked_at=checked_at,
            adapter_hash=adapter_hash,
            summary=ingest_error,
            details={"sessions": len(sessions), "scoped_paths": len(scoped_paths)},
        )

    return ValidationResult(
        source=source,
        ok=True,
        checked_at=checked_at,
        adapter_hash=adapter_hash,
        summary="strict validation passed",
        details={"sessions": len(sessions), "scoped_paths": len(scoped_paths)},
    )


def _instantiate_adapter(
    adapter_cls: type[ObserveAdapter],
    source_paths: list[Path],
) -> ObserveAdapter:
    fake_db = _FakeDB()
    primary = source_paths[0] if source_paths else None
    roots = sorted({p.parent for p in source_paths if p.exists()}) if source_paths else []
    for kwarg, value in [
        ("source_db_path", primary),
        ("data_dir", primary),
        ("source_roots", roots or None),
    ]:
        if value is None:
            continue
        try:
            return adapter_cls(fake_db, "__validate__", **{kwarg: value})
        except TypeError:
            pass
    adapter = adapter_cls(fake_db, "__validate__")
    if primary is not None:
        if hasattr(adapter, "source_db_path"):
            adapter.source_db_path = primary
        if hasattr(adapter, "data_dir"):
            adapter.data_dir = primary
    return adapter


def _validate_sessions(adapter: ObserveAdapter, sessions: list[Any]) -> str | None:
    for session in sessions:
        if not getattr(session, "session_id", None):
            return "session missing session_id"
        if not getattr(session, "source_path", None):
            return "session missing source_path"
        if not getattr(session, "start_time", None):
            return "session missing start_time"
        turns = list(getattr(session, "turns", []) or [])
        if not turns:
            return "session produced no turns"
        for turn in turns:
            if not getattr(turn, "role", None):
                return "turn missing role"
            if not isinstance(getattr(turn, "content", None), str):
                return "turn missing textual content"
            if not getattr(turn, "timestamp", None):
                return "turn missing timestamp"
        events = adapter.session_to_events(session)
        if not events:
            return "session_to_events produced no events"
        for event in events:
            if not getattr(event, "timestamp", None):
                return "event missing timestamp"
            if not getattr(event, "event_type", None):
                return "event missing event_type"
            if not getattr(event, "session_id", None):
                return "event missing session_id"
            if not getattr(event, "source_path", None):
                return "event missing source_path"
            if not getattr(event, "external_id", None):
                return "event missing external_id"
    return None


def _validate_ingest_stability(
    adapter_cls: type[ObserveAdapter],
    source: str,
    source_paths: list[Path],
    sessions: list[Any],
) -> str | None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "validate.db"
        events_path = Path(td) / "events.db"
        db = SykeDB(db_path, event_db_path=events_path)
        db.initialize()
        try:
            adapter = _instantiate_real_adapter(adapter_cls, db, source_paths)
            first_inserted = 0
            second_inserted = 0
            for session in sessions:
                first_inserted += adapter._ingest_session(session)  # type: ignore[attr-defined]
            for session in sessions:
                second_inserted += adapter._ingest_session(session)  # type: ignore[attr-defined]
            if first_inserted <= 0:
                return "ingest produced no events"
            if second_inserted != 0:
                return "repeated ingest on unchanged data produced new events"
        finally:
            db.close()
    return None


def _validation_scope(source_paths: list[Path], max_paths: int = 20) -> list[Path]:
    if not source_paths:
        return []

    scored: list[tuple[float, Path]] = []
    for path in source_paths:
        try:
            scored.append((path.stat().st_mtime, path))
        except OSError:
            continue
    scored.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in scored[:max_paths]]


def _sample_sessions(adapter: ObserveAdapter, scoped_paths: list[Path]) -> list[Any]:
    return list(
        itertools.islice(
            adapter.iter_sessions(since=0, paths=scoped_paths),
            _MAX_SESSION_SAMPLE,
        )
    )


def _instantiate_real_adapter(
    adapter_cls: type[ObserveAdapter],
    db: SykeDB,
    source_paths: list[Path],
) -> ObserveAdapter:
    primary = source_paths[0] if source_paths else None
    try:
        if primary is not None:
            return adapter_cls(db, "__validate__", source_db_path=primary)
    except TypeError:
        pass
    try:
        if primary is not None:
            return adapter_cls(db, "__validate__", data_dir=primary)
    except TypeError:
        pass
    adapter = adapter_cls(db, "__validate__")
    if primary is not None:
        if hasattr(adapter, "source_db_path"):
            adapter.source_db_path = primary
        if hasattr(adapter, "data_dir"):
            adapter.data_dir = primary
    return adapter


def _file_hash(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
