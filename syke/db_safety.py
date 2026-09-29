"""Recovery points and deterministic gates for the mutable graph database."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

from syke.config import user_control_dir, user_data_dir
from syke.control import (
    FINAL_RECEIPT_STATUSES,
    _fsync_directory,
    _safe_id,
    _write_json_once,
    get_receipt,
    receipt_path,
    write_receipt,
)
from syke.db import GRAPH_IDENTITY_TABLES, SCHEMA_VERSION, _validate_current_schema
from syke.db_access import (
    DatabaseLease,
    DatabaseLeaseUnavailable,
    acquire_database_lease,
    database_lock_path,
    maintenance_marker_path,
)
from syke.memory.memex_budget import (
    MEMEX_TOKEN_ENCODING,
    MEMORY_TOKEN_LIMIT,
    count_memory_tokens,
    format_memex_projection,
    measure_memex,
    strip_memex_header,
)

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows platforms
    msvcrt = None

MAX_FULL_COPY_FALLBACK_BYTES = 64 * 1024 * 1024
RECOVERY_IN_PROGRESS_FILENAME = "synthesis-in-progress.json"
RECOVERY_ARTIFACT_SUFFIXES = (
    ".json",
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite.tmp",
)


@dataclass
class MemoryFingerprint:
    id: str
    content_hash: str
    created_at: str
    updated_at: str | None
    tokens: int


@dataclass
class LinkFingerprint:
    id: str
    source_id: str
    target_id: str
    reason_hash: str
    created_at: str


@dataclass
class MemexFingerprint:
    id: str
    content_hash: str
    created_at: str
    updated_at: str | None


@dataclass
class StateBaseline:
    user_id: str
    identity_user_id: str | None
    captured_at: str
    memories: dict[str, MemoryFingerprint]
    links: dict[str, LinkFingerprint]
    current_memex: MemexFingerprint | None


@dataclass
class RecoveryPoint:
    id: str
    user_id: str
    cycle_id: str | None
    db_path: str
    backup_path: str
    manifest_path: str
    created_at: str
    method: str
    size_bytes: int


@dataclass(frozen=True)
class RecoveryInProgress:
    cycle_id: str
    recovery_point_id: str
    started_at: str


class SynthesisLockUnavailable(RuntimeError):
    """Raised when another synthesis cycle owns the cross-process lock."""


def synthesis_lock_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "synthesis.lock"


def acquire_synthesis_lock(user_id: str) -> tuple[TextIO, Path]:
    """Acquire the per-user synthesis lock shared by recovery and execution."""
    lock_path = synthesis_lock_path(user_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SynthesisLockUnavailable(str(lock_path)) from exc
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            try:
                windows_lock = cast(Any, msvcrt)
                if lock_path.stat().st_size == 0:
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                windows_lock.locking(handle.fileno(), windows_lock.LK_NBLCK, 1)
            except OSError as exc:
                raise SynthesisLockUnavailable(str(lock_path)) from exc
        else:  # pragma: no cover - unsupported platform
            logger.warning(
                "No synthesis lock backend available; recovery cannot distinguish live cycles"
            )
            return handle, lock_path

        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\t{datetime.now(UTC).isoformat()}\n")
        handle.flush()
        return handle, lock_path
    except Exception:
        handle.close()
        raise


def release_synthesis_lock(handle: TextIO) -> None:
    """Release a lock returned by :func:`acquire_synthesis_lock`."""
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            windows_lock = cast(Any, msvcrt)
            handle.seek(0)
            windows_lock.locking(handle.fileno(), windows_lock.LK_UNLCK, 1)
    finally:
        handle.close()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _hash_text(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _capture_memories(db: Any, user_id: str) -> dict[str, MemoryFingerprint]:
    rows = db.conn.execute(
        """SELECT id, content, created_at, updated_at
           FROM memories
           WHERE user_id = ?
           ORDER BY id""",
        (user_id,),
    ).fetchall()
    return {
        str(row["id"]): MemoryFingerprint(
            id=str(row["id"]),
            content_hash=_hash_text(row["content"]),
            created_at=_text(row["created_at"]),
            updated_at=_text(row["updated_at"]) or None,
            tokens=count_memory_tokens(_text(row["content"])),
        )
        for row in rows
    }


def _capture_links(db: Any, user_id: str) -> dict[str, LinkFingerprint]:
    rows = db.conn.execute(
        """SELECT link.id, link.source_id, link.target_id, link.reason, link.created_at
           FROM links AS link
           WHERE link.user_id = ?
           ORDER BY link.id""",
        (user_id,),
    ).fetchall()
    return {
        str(row["id"]): LinkFingerprint(
            id=str(row["id"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            reason_hash=_hash_text(row["reason"]),
            created_at=_text(row["created_at"]),
        )
        for row in rows
    }


def _recovery_dir(user_id: str) -> Path:
    path = user_control_dir(user_id) / "recovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def capture_baseline(db: Any, user_id: str) -> StateBaseline:
    """Capture the transient pre-agent shape used by the post-cycle gate."""
    _validate_current_schema(db.conn)
    identity_row = db.conn.execute(
        "SELECT user_id FROM syke_identity WHERE singleton = 1"
    ).fetchone()
    if identity_row is None:
        raise ValueError("Current graph must be bound to one Syke identity before synthesis")
    if str(identity_row["user_id"]) != user_id:
        raise ValueError(
            f"Current graph identity {identity_row['user_id']!r} does not match {user_id!r}"
        )
    memex_row = db.conn.execute(
        """SELECT id, content, created_at, updated_at
           FROM current_memex
           WHERE singleton = 1 AND user_id = ?""",
        (user_id,),
    ).fetchone()
    current_memex = (
        MemexFingerprint(
            id=str(memex_row["id"]),
            content_hash=_hash_text(strip_memex_header(_text(memex_row["content"]))),
            created_at=_text(memex_row["created_at"]),
            updated_at=_text(memex_row["updated_at"]) or None,
        )
        if memex_row is not None
        else None
    )

    return StateBaseline(
        user_id=user_id,
        identity_user_id=str(identity_row["user_id"]),
        captured_at=datetime.now(UTC).isoformat(),
        memories=_capture_memories(db, user_id),
        links=_capture_links(db, user_id),
        current_memex=current_memex,
    )


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _make_db_file_stable(conn: sqlite3.Connection) -> None:
    """Flush connection-visible state into the main DB file before cloning."""
    conn.commit()
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError("could not make DB file stable before recovery clone") from exc
    if row is not None and len(row) > 0 and int(row[0] or 0) != 0:
        raise RuntimeError("database file remained busy before recovery clone")
    conn.commit()


def _fsync_file(path: Path) -> None:
    """Force a freshly copied file's bytes to disk before it is renamed into place."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _try_copy_on_write_clone(source: Path, destination: Path) -> bool:
    if sys.platform != "darwin":
        return False
    _unlink_if_exists(destination)
    try:
        completed = subprocess.run(
            ["cp", "-c", str(source), str(destination)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not destination.exists():
            _unlink_if_exists(destination)
            return False
    except OSError:
        _unlink_if_exists(destination)
        return False
    return True


def _sqlite_backup_copy(db: Any, destination: Path) -> None:
    _unlink_if_exists(destination)
    with closing(sqlite3.connect(str(destination))) as backup_conn:
        db.conn.backup(backup_conn)


def _sqlite_checks(path: Path) -> dict[str, str | None]:
    with closing(sqlite3.connect(str(path))) as conn:
        return _connection_checks(conn)


def _connection_checks(conn: sqlite3.Connection) -> dict[str, str | None]:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    quick = conn.execute("PRAGMA quick_check").fetchone()
    return {
        "integrity_check": integrity[0] if integrity else None,
        "quick_check": quick[0] if quick else None,
    }


def is_search_index_integrity_issue(value: Any) -> bool:
    text = _text(value).lower()
    return "memories_fts" in text and (
        "malformed inverted index" in text
        or "fts5: corruption found reading blob" in text
        or "fts5: checksum mismatch" in text
    )


def _checks_are_search_index_only(checks: dict[str, str | None]) -> bool:
    failed = [
        value
        for value in (checks.get("integrity_check"), checks.get("quick_check"))
        if value and value != "ok"
    ]
    return bool(failed) and all(is_search_index_integrity_issue(value) for value in failed)


def _append_check_issues(issues: list[str], checks: dict[str, str | None]) -> None:
    if checks["integrity_check"] != "ok":
        issues.append(f"integrity_check failed: {checks['integrity_check']}")
    if checks["quick_check"] != "ok":
        issues.append(f"quick_check failed: {checks['quick_check']}")


def _checks_ok(checks: dict[str, str | None]) -> bool:
    return checks["integrity_check"] == "ok" and checks["quick_check"] == "ok"


def _require_checks_ok(checks: dict[str, str | None], label: str) -> None:
    if not _checks_ok(checks):
        raise ValueError(
            f"{label} failed SQLite checks: "
            f"integrity_check={checks['integrity_check']}, quick_check={checks['quick_check']}"
        )


def _search_index_matches_graph(db: Any) -> bool:
    expected = [
        (str(row["id"]), _text(row["content"]))
        for row in db.conn.execute("SELECT id, content FROM memories ORDER BY id").fetchall()
    ]
    actual = [
        (str(row["memory_id"]), _text(row["content"]))
        for row in db.conn.execute(
            "SELECT memory_id, content FROM memories_fts ORDER BY memory_id, rowid"
        ).fetchall()
    ]
    return actual == expected


def _repair_search_index_if_needed(db: Any) -> tuple[dict[str, str | None], bool, int]:
    checks = _connection_checks(db.conn)
    search_corruption = _checks_are_search_index_only(checks)
    if not _checks_ok(checks) and not search_corruption:
        return checks, False, 0
    if not search_corruption and _search_index_matches_graph(db):
        return checks, False, 0

    rebuilt_rows = _rebuild_search_index(db)
    return _connection_checks(db.conn), True, rebuilt_rows


def _require_sqlite_ok(path: Path, label: str) -> dict[str, str | None]:
    checks = _sqlite_checks(path)
    _require_checks_ok(checks, label)
    return checks


def _copy_recovery_db(
    db: Any,
    db_path: Path,
    destination: Path,
    *,
    max_full_copy_fallback_bytes: int,
) -> str:
    source_size = db_path.stat().st_size
    stable_error: Exception | None = None
    try:
        _make_db_file_stable(db.conn)
    except Exception as exc:
        stable_error = exc

    if stable_error is None and _try_copy_on_write_clone(db_path, destination):
        return "copy_on_write_clone"

    if source_size > max_full_copy_fallback_bytes:
        reason = (
            str(stable_error)
            if stable_error is not None
            else "copy-on-write recovery clone unavailable"
        )
        raise RuntimeError(f"{reason}; refusing full-copy fallback for {source_size} byte database")

    _sqlite_backup_copy(db, destination)
    return "sqlite_backup_fallback"


def create_recovery_point(
    db: Any,
    user_id: str,
    *,
    run_id: str,
    cycle_id: str | None,
    max_full_copy_fallback_bytes: int = MAX_FULL_COPY_FALLBACK_BYTES,
) -> RecoveryPoint:
    """Create a cheap local recovery copy for the current cycle."""
    run_id = _safe_id(run_id, label="recovery")
    if cycle_id is not None:
        cycle_id = _safe_id(cycle_id, label="cycle")
    db_path = Path(str(db.db_path)).expanduser().resolve()
    if str(db.db_path) == ":memory:":
        raise ValueError("Recovery points require a file-backed SQLite database")
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))

    _validate_current_schema(db.conn)
    source_checks, search_index_rebuilt, _ = _repair_search_index_if_needed(db)
    _require_checks_ok(source_checks, "Source database")

    created_at = datetime.now(UTC).isoformat()
    recovery_dir = _recovery_dir(user_id)
    backup_path = recovery_dir / f"{run_id}.sqlite"
    tmp_backup_path = recovery_dir / f".{run_id}.sqlite.tmp"
    manifest_path = recovery_dir / f"{run_id}.json"

    _unlink_if_exists(backup_path)
    _unlink_if_exists(tmp_backup_path)
    method = _copy_recovery_db(
        db,
        db_path,
        tmp_backup_path,
        max_full_copy_fallback_bytes=max_full_copy_fallback_bytes,
    )
    _fsync_file(tmp_backup_path)
    os.replace(tmp_backup_path, backup_path)
    _fsync_directory(recovery_dir)
    size_bytes = backup_path.stat().st_size
    backup_checks = _require_sqlite_ok(backup_path, "Recovery point")

    point = RecoveryPoint(
        id=run_id,
        user_id=user_id,
        cycle_id=cycle_id,
        db_path=str(db_path),
        backup_path=str(backup_path),
        manifest_path=str(manifest_path),
        created_at=created_at,
        method=method,
        size_bytes=size_bytes,
    )
    manifest = {
        "recovery_point": asdict(point),
        "source_checks": source_checks,
        "backup_checks": backup_checks,
        "search_index_rebuilt": search_index_rebuilt,
    }
    _write_json_once(manifest_path, manifest)
    return point


def rotate_recovery_points(user_id: str, *, keep_id: str) -> None:
    """Keep only the explicitly selected recovery bundle."""
    recovery_dir = _recovery_dir(user_id)
    keep_id = _safe_id(keep_id, label="recovery")

    keep_paths = {
        recovery_dir / f"{keep_id}.json",
        recovery_dir / f"{keep_id}.sqlite",
    }
    missing = sorted(str(path) for path in keep_paths if not path.is_file())
    if missing:
        raise FileNotFoundError(
            "Cannot retain a complete recovery bundle; missing: " + ", ".join(missing)
        )

    for candidate in recovery_dir.iterdir():
        if candidate in keep_paths or not candidate.is_file():
            continue
        if candidate.name.endswith(RECOVERY_ARTIFACT_SUFFIXES):
            candidate.unlink()
    _fsync_directory(recovery_dir)


def restore_recovery_point(
    point: RecoveryPoint,
    *,
    exclusive_lease: DatabaseLease | None = None,
) -> dict[str, Any]:
    """Restore the database file from a recovery point."""
    backup_path = Path(point.backup_path)
    db_path = Path(point.db_path)
    if not backup_path.exists():
        raise FileNotFoundError(str(backup_path))
    actual_size = backup_path.stat().st_size
    if actual_size != point.size_bytes:
        raise ValueError("Recovery point size mismatch")
    backup_checks = _require_sqlite_ok(backup_path, "Recovery point")

    owns_lease = exclusive_lease is None
    if exclusive_lease is not None:
        if not isinstance(exclusive_lease, DatabaseLease):
            raise TypeError("Caller-owned recovery lease must be a DatabaseLease")
        if exclusive_lease.released:
            raise ValueError("Caller-owned recovery lease is already released")
        if not exclusive_lease.exclusive:
            raise ValueError("Caller-owned recovery lease must be exclusive")
        if exclusive_lease.lock_path != database_lock_path(db_path):
            raise ValueError("Caller-owned recovery lease does not match recovery database")
        lease = exclusive_lease
    else:
        try:
            lease = acquire_database_lease(db_path, exclusive=True, blocking=False)
        except DatabaseLeaseUnavailable as exc:
            raise RuntimeError(
                f"Cannot restore {db_path}: active database users hold shared leases"
            ) from exc

    try:
        tmp_path = db_path.with_name(f"{db_path.name}.restore-tmp")
        _unlink_if_exists(tmp_path)
        try:
            if not _try_copy_on_write_clone(backup_path, tmp_path):
                shutil.copy2(backup_path, tmp_path)
            for suffix in ("-wal", "-shm"):
                _unlink_if_exists(Path(f"{db_path}{suffix}"))
            _fsync_file(tmp_path)
            os.replace(tmp_path, db_path)
            _fsync_directory(db_path.parent)
            restored_checks = _require_sqlite_ok(db_path, "Restored database")
        finally:
            _unlink_if_exists(tmp_path)
            for suffix in ("-wal", "-shm"):
                _unlink_if_exists(Path(f"{tmp_path}{suffix}"))
    finally:
        if owns_lease:
            lease.release()
    result = {
        "restored": True,
        "recovery_point": point.id,
        "integrity_check": restored_checks["integrity_check"],
        "quick_check": restored_checks["quick_check"],
        "backup_integrity_check": backup_checks["integrity_check"],
        "backup_quick_check": backup_checks["quick_check"],
    }
    return result


def recovery_in_progress_path(user_id: str) -> Path:
    return user_control_dir(user_id) / RECOVERY_IN_PROGRESS_FILENAME


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {label}: {path}")
    return value


def load_recovery_point(user_id: str, recovery_point_id: str) -> RecoveryPoint:
    """Load and verify one protected recovery bundle."""
    identifier = _safe_id(recovery_point_id, label="recovery")
    recovery_dir = _recovery_dir(user_id)
    manifest_path = recovery_dir / f"{identifier}.json"
    backup_path = recovery_dir / f"{identifier}.sqlite"
    manifest = _read_json_object(manifest_path, label="recovery manifest")
    raw_point = manifest.get("recovery_point")
    if not isinstance(raw_point, dict):
        raise ValueError(f"Recovery manifest has no recovery point: {manifest_path}")
    try:
        point = RecoveryPoint(
            id=str(raw_point["id"]),
            user_id=str(raw_point["user_id"]),
            cycle_id=(
                str(raw_point["cycle_id"]) if raw_point.get("cycle_id") is not None else None
            ),
            db_path=str(raw_point["db_path"]),
            backup_path=str(raw_point["backup_path"]),
            manifest_path=str(raw_point["manifest_path"]),
            created_at=str(raw_point["created_at"]),
            method=str(raw_point["method"]),
            size_bytes=int(raw_point["size_bytes"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Malformed recovery point: {manifest_path}") from exc

    if point.id != identifier or point.user_id != user_id:
        raise ValueError("Recovery point identity mismatch")
    if point.cycle_id is not None:
        _safe_id(point.cycle_id, label="cycle")
    if Path(point.manifest_path).expanduser().resolve() != manifest_path.resolve():
        raise ValueError("Recovery manifest path mismatch")
    if Path(point.backup_path).expanduser().resolve() != backup_path.resolve():
        raise ValueError("Recovery backup path mismatch")
    if not Path(point.db_path).expanduser().is_absolute():
        raise ValueError("Recovery database path must be absolute")
    if not backup_path.is_file():
        raise FileNotFoundError(str(backup_path))
    if backup_path.stat().st_size != point.size_bytes:
        raise ValueError("Recovery point size mismatch")
    _require_sqlite_ok(backup_path, "Recovery point")
    return point


def _require_timestamp(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    return value


def mark_recovery_in_progress(point: RecoveryPoint, *, started_at: str) -> Path:
    """Publish the crash marker only after the retained recovery verifies."""
    if point.cycle_id is None:
        raise ValueError("An in-progress recovery marker requires a cycle id")
    verified = load_recovery_point(point.user_id, point.id)
    if asdict(verified) != asdict(point):
        raise ValueError("Recovery point does not match its protected manifest")
    marker = {
        "schema_version": 1,
        "cycle_id": _safe_id(point.cycle_id, label="cycle"),
        "recovery_point_id": _safe_id(point.id, label="recovery"),
        "started_at": _require_timestamp(started_at, label="synthesis start"),
    }
    return _write_json_once(recovery_in_progress_path(point.user_id), marker)


def load_recovery_in_progress(user_id: str) -> RecoveryInProgress | None:
    path = recovery_in_progress_path(user_id)
    if not path.is_file():
        return None
    value = _read_json_object(path, label="synthesis recovery marker")
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported synthesis recovery marker version")
    return RecoveryInProgress(
        cycle_id=_safe_id(value.get("cycle_id"), label="cycle"),
        recovery_point_id=_safe_id(value.get("recovery_point_id"), label="recovery"),
        started_at=_require_timestamp(value.get("started_at"), label="synthesis start"),
    )


def clear_recovery_in_progress(user_id: str, *, cycle_id: str) -> bool:
    expected_cycle = _safe_id(cycle_id, label="cycle")
    marker = load_recovery_in_progress(user_id)
    if marker is None:
        return False
    if marker.cycle_id != expected_cycle:
        raise RuntimeError(
            f"Cannot clear recovery marker for {marker.cycle_id!r} as {expected_cycle!r}"
        )
    path = recovery_in_progress_path(user_id)
    path.unlink()
    _fsync_directory(path.parent)
    return True


def publish_synthesis_recovery_fence(
    db_path: str | Path,
    *,
    cycle_id: str,
) -> Path:
    """Block new database opens while an interrupted cycle is restored."""
    path = maintenance_marker_path(db_path)
    return _write_json_once(
        path,
        {
            "schema_version": 1,
            "kind": "synthesis_recovery",
            "cycle_id": _safe_id(cycle_id, label="cycle"),
        },
    )


def clear_synthesis_recovery_fence(
    db_path: str | Path,
    *,
    cycle_id: str,
) -> bool:
    """Clear only the matching synthesis-recovery maintenance marker."""
    path = maintenance_marker_path(db_path)
    if not path.is_file():
        return False
    value = _read_json_object(path, label="database maintenance marker")
    expected_cycle = _safe_id(cycle_id, label="cycle")
    if value.get("kind") != "synthesis_recovery" or value.get("cycle_id") != expected_cycle:
        raise RuntimeError(f"Refusing to clear an unrelated database maintenance marker: {path}")
    path.unlink()
    _fsync_directory(path.parent)
    return True


def _read_recovered_memex(db_path: Path, user_id: str) -> str | None:
    uri = f"file:{db_path}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            raise ValueError(f"Cannot recover MEMEX from unsupported schema version {version}")
        row = conn.execute(
            "SELECT content FROM current_memex WHERE singleton = 1 AND user_id = ?",
            (user_id,),
        ).fetchone()
        return strip_memex_header(_text(row[0])) if row is not None else None


def _write_recovered_memex(memex_path: Path, content: str | None) -> None:
    memex_path = memex_path.expanduser().resolve()
    memex_path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        existed = memex_path.exists()
        memex_path.unlink(missing_ok=True)
        if existed:
            _fsync_directory(memex_path.parent)
        return

    temporary = memex_path.with_name(f".{memex_path.name}.{os.getpid()}.tmp")
    _unlink_if_exists(temporary)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(format_memex_projection(content) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, memex_path)
        _fsync_directory(memex_path.parent)
    finally:
        _unlink_if_exists(temporary)


def reconcile_interrupted_synthesis(
    user_id: str,
    *,
    memex_path: str | Path,
    expected_db_path: str | Path | None = None,
    completed_at_override: str | None = None,
    exclusive_lease: DatabaseLease | None = None,
) -> dict[str, Any]:
    """Restore only a marked cycle that has no protected final receipt."""
    marker = load_recovery_in_progress(user_id)
    if marker is None:
        return {"action": "none"}

    control_dir = user_control_dir(user_id)
    receipt = get_receipt(control_dir, marker.cycle_id)
    if receipt is None and receipt_path(control_dir, marker.cycle_id).exists():
        # A receipt that exists but cannot be read is not evidence that the
        # cycle never finished. Restoring over it could discard accepted state.
        raise RuntimeError(
            f"Final receipt for cycle {marker.cycle_id} exists but is unreadable; "
            "refusing to restore the recovery point"
        )
    if isinstance(receipt, dict) and receipt.get("status") in FINAL_RECEIPT_STATUSES:
        clear_recovery_in_progress(user_id, cycle_id=marker.cycle_id)
        return {"action": "finalized", "cycle_id": marker.cycle_id}

    point = load_recovery_point(user_id, marker.recovery_point_id)
    if point.cycle_id != marker.cycle_id:
        raise ValueError("Recovery marker and recovery point identify different cycles")
    if (
        expected_db_path is not None
        and Path(point.db_path).resolve() != Path(expected_db_path).expanduser().resolve()
    ):
        raise ValueError("Recovery point does not target the expected database")

    restore_info = restore_recovery_point(point, exclusive_lease=exclusive_lease)
    _write_recovered_memex(
        Path(memex_path),
        _read_recovered_memex(Path(point.db_path), user_id),
    )
    completed_at = _require_timestamp(
        completed_at_override or datetime.now(UTC).isoformat(),
        label="reconciliation completion",
    )
    final_receipt = {
        "id": marker.cycle_id,
        "started_at": marker.started_at,
        "completed_at": completed_at,
        "status": "incomplete",
        "session_id": None,
        "acknowledged_record_ids": [],
        "memex_updated": False,
        "recovery": {
            "restored": True,
            "recovery_point": point.id,
        },
        "error": "Recovered an interrupted synthesis before database use",
    }
    write_receipt(control_dir, final_receipt)
    clear_recovery_in_progress(user_id, cycle_id=marker.cycle_id)
    return {
        "action": "restored",
        "cycle_id": marker.cycle_id,
        "recovery": restore_info,
    }


def try_reconcile_before_database_use(
    user_id: str,
    *,
    memex_path: str | Path,
    expected_db_path: str | Path | None = None,
    completed_at_override: str | None = None,
) -> dict[str, Any]:
    """Reconcile a stale marker without mistaking a live synthesis for a crash."""
    marker = load_recovery_in_progress(user_id)
    if marker is None:
        return {"action": "none"}

    if expected_db_path is not None:
        db_path = Path(expected_db_path).expanduser().resolve()
    else:
        point = load_recovery_point(user_id, marker.recovery_point_id)
        db_path = Path(point.db_path).expanduser().resolve()

    try:
        lease = acquire_database_lease(db_path, exclusive=True, blocking=False)
    except DatabaseLeaseUnavailable as lease_error:
        try:
            lock_handle, _ = acquire_synthesis_lock(user_id)
        except SynthesisLockUnavailable:
            return {"action": "active", "cycle_id": marker.cycle_id}
        else:
            release_synthesis_lock(lock_handle)
            raise RuntimeError(
                f"Cannot reconcile interrupted synthesis while database users are active: {db_path}"
            ) from lease_error

    try:
        try:
            lock_handle, _ = acquire_synthesis_lock(user_id)
        except SynthesisLockUnavailable:
            return {"action": "active", "cycle_id": marker.cycle_id}

        try:
            result = reconcile_interrupted_synthesis(
                user_id,
                memex_path=memex_path,
                expected_db_path=db_path,
                completed_at_override=completed_at_override,
                exclusive_lease=lease,
            )
            if result.get("action") in {"finalized", "restored"}:
                clear_synthesis_recovery_fence(db_path, cycle_id=marker.cycle_id)
            return result
        finally:
            release_synthesis_lock(lock_handle)
    finally:
        lease.release()


def _rebuild_search_index(db: Any) -> int:
    """Repair the derived FTS cache from the durable memories table."""
    db.conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    db.conn.execute("DELETE FROM memories_fts")
    cursor = db.conn.execute(
        """INSERT INTO memories_fts(memory_id, content)
           SELECT id, content FROM memories"""
    )
    db.conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
    if not getattr(db, "_in_transaction", False):
        db.conn.commit()
    return int(cursor.rowcount or 0)


def validate_state_after_cycle(
    db: Any,
    user_id: str,
    baseline: StateBaseline,
    *,
    allow_empty_memex: bool = False,
) -> dict[str, Any]:
    """Validate the v3 current graph after one agent attempt."""
    issues: list[str] = []
    stats: dict[str, Any] = {
        "baseline_memories": len(baseline.memories),
        "baseline_links": len(baseline.links),
        "baseline_memex_count": int(baseline.current_memex is not None),
    }

    try:
        _validate_current_schema(db.conn)
        stats["schema_valid"] = True
    except Exception as exc:
        stats["schema_valid"] = False
        issues.append(f"current graph schema changed: {exc}")

    try:
        foreign_key_rows = db.conn.execute("PRAGMA foreign_key_check").fetchall()
        stats["foreign_key_violations"] = len(foreign_key_rows)
        if foreign_key_rows:
            issues.append(f"foreign key violations exist: {len(foreign_key_rows)}")
        checks, search_index_rebuilt, search_index_rebuilt_rows = _repair_search_index_if_needed(db)
        stats.update(checks)
        stats["search_index_rebuilt"] = search_index_rebuilt
        if search_index_rebuilt:
            stats["search_index_rebuilt_rows"] = search_index_rebuilt_rows
        _append_check_issues(issues, checks)
        search_index_matches = _search_index_matches_graph(db)
        stats["search_index_matches_graph"] = search_index_matches
        if not search_index_matches:
            issues.append("memories_fts does not match the current memories table")
    except sqlite3.Error as exc:
        issues.append(f"database validation error: {exc}")
        return {"valid": False, "issues": issues, "stats": stats}

    identity_rows = db.conn.execute(
        "SELECT singleton, user_id FROM syke_identity ORDER BY singleton"
    ).fetchall()
    stats["identity_rows"] = len(identity_rows)
    stats["identity_user_id"] = (
        str(identity_rows[0]["user_id"]) if len(identity_rows) == 1 else None
    )
    if baseline.identity_user_id is not None:
        if len(identity_rows) != 1:
            issues.append(f"single Syke identity missing or duplicated: {len(identity_rows)} rows")
        elif str(identity_rows[0]["user_id"]) != baseline.identity_user_id:
            issues.append(
                "Syke identity changed: "
                f"{baseline.identity_user_id!r} -> {identity_rows[0]['user_id']!r}"
            )
        rows_outside_identity = {
            table: int(
                db.conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id != ?",
                    (baseline.identity_user_id,),
                ).fetchone()[0]
            )
            for table in GRAPH_IDENTITY_TABLES
        }
        stats["rows_outside_identity"] = rows_outside_identity
        if any(rows_outside_identity.values()):
            issues.append(f"rows exist outside the single Syke identity: {rows_outside_identity}")

    try:
        memories = _capture_memories(db, user_id)
        links = _capture_links(db, user_id)
    except sqlite3.Error as exc:
        issues.append(f"current graph read failed: {exc}")
        return {"valid": False, "issues": issues, "stats": stats}

    baseline_memory_ids = set(baseline.memories)
    memory_ids = set(memories)
    created_memory_ids = sorted(memory_ids - baseline_memory_ids)
    removed_memory_ids = sorted(baseline_memory_ids - memory_ids)
    revised_memory_ids = sorted(
        memory_id
        for memory_id in baseline_memory_ids & memory_ids
        if memories[memory_id].content_hash != baseline.memories[memory_id].content_hash
    )
    changed_memory_created_at_ids = sorted(
        memory_id
        for memory_id in baseline_memory_ids & memory_ids
        if memories[memory_id].created_at != baseline.memories[memory_id].created_at
    )
    stats["memories_created"] = len(created_memory_ids)
    stats["memories_updated"] = len(revised_memory_ids)
    stats["memories_removed"] = len(removed_memory_ids)
    stats["removed_memory_ids"] = removed_memory_ids
    stats["changed_memory_created_at_ids"] = changed_memory_created_at_ids
    if changed_memory_created_at_ids:
        issues.append(
            f"pre-existing memory created_at changed: {len(changed_memory_created_at_ids)}"
        )

    # A memory already over budget at cycle start may stay as large, never larger.
    memories_over_budget: list[dict[str, Any]] = []
    for memory_id in (*created_memory_ids, *revised_memory_ids):
        tokens = memories[memory_id].tokens
        before = baseline.memories.get(memory_id)
        tokens_before = before.tokens if before is not None else None
        allowed = max(MEMORY_TOKEN_LIMIT, tokens_before or 0)
        if tokens <= allowed:
            continue
        memories_over_budget.append(
            {"id": memory_id, "tokens": tokens, "tokens_before": tokens_before}
        )
        if allowed > MEMORY_TOKEN_LIMIT:
            issues.append(
                f"memory {memory_id} grew from {tokens_before} to {tokens} tokens "
                f"({MEMEX_TOKEN_ENCODING}); a memory over the {MEMORY_TOKEN_LIMIT}-token "
                "budget may not grow"
            )
        else:
            issues.append(
                f"memory {memory_id} over budget: {tokens}/{MEMORY_TOKEN_LIMIT} tokens "
                f"({MEMEX_TOKEN_ENCODING})"
            )
    stats["memory_token_limit"] = MEMORY_TOKEN_LIMIT
    stats["memories_over_budget"] = memories_over_budget

    baseline_link_ids = set(baseline.links)
    link_ids = set(links)
    created_link_ids = sorted(link_ids - baseline_link_ids)
    removed_link_ids = sorted(baseline_link_ids - link_ids)
    revised_link_ids = sorted(
        link_id
        for link_id in baseline_link_ids & link_ids
        if links[link_id] != baseline.links[link_id]
    )
    changed_link_created_at_ids = sorted(
        link_id
        for link_id in baseline_link_ids & link_ids
        if links[link_id].created_at != baseline.links[link_id].created_at
    )
    stats["links_created"] = len(created_link_ids)
    stats["links_updated"] = len(revised_link_ids)
    stats["links_removed"] = len(removed_link_ids)
    stats["changed_link_created_at_ids"] = changed_link_created_at_ids
    if changed_link_created_at_ids:
        issues.append(f"pre-existing link created_at changed: {len(changed_link_created_at_ids)}")

    broken_link_rows = [
        {
            "id": _text(row["id"]),
            "source_id": _text(row["source_id"]),
            "target_id": _text(row["target_id"]),
        }
        for row in db.conn.execute(
            """SELECT link.id, link.source_id, link.target_id
               FROM links AS link
               LEFT JOIN memories AS source
                 ON source.id = link.source_id AND source.user_id = link.user_id
               LEFT JOIN memories AS target
                 ON target.id = link.target_id AND target.user_id = link.user_id
               WHERE link.user_id = ? AND (source.id IS NULL OR target.id IS NULL)
               ORDER BY link.id""",
            (user_id,),
        ).fetchall()
    ]
    stats["broken_links"] = len(broken_link_rows)
    stats["broken_link_rows"] = broken_link_rows
    if broken_link_rows:
        issues.append(f"links reference missing memories: {len(broken_link_rows)}")

    stats["graph_change"] = {
        "created_memory_ids": created_memory_ids,
        "revised_memory_ids": revised_memory_ids,
        "created_link_ids": created_link_ids,
        "revised_link_ids": revised_link_ids,
        "removed_link_ids": removed_link_ids,
    }

    memex_rows = db.conn.execute(
        """SELECT singleton, id, user_id, content, created_at, updated_at
           FROM current_memex
           WHERE user_id = ?
           ORDER BY singleton""",
        (user_id,),
    ).fetchall()
    stats["current_memex_count"] = len(memex_rows)
    memex_row = memex_rows[0] if len(memex_rows) == 1 else None
    if len(memex_rows) > 1:
        issues.append(f"duplicate current MEMEX rows: {len(memex_rows)}")

    if baseline.current_memex is not None:
        if memex_row is None:
            issues.append("pre-existing current MEMEX missing")
        else:
            if str(memex_row["id"]) != baseline.current_memex.id:
                issues.append(
                    "current MEMEX identity changed: "
                    f"{baseline.current_memex.id} -> {memex_row['id']}"
                )
            if _text(memex_row["created_at"]) != baseline.current_memex.created_at:
                issues.append("current MEMEX created_at changed")
    elif memex_row is not None:
        stats["new_memex_rows"] = 1
    else:
        stats["new_memex_rows"] = 0

    if memex_row is None:
        if not allow_empty_memex:
            issues.append("current MEMEX missing")
    else:
        memex_body = strip_memex_header(_text(memex_row["content"]))
        measurement = measure_memex(memex_body)
        stats["memex_tokens"] = measurement["tokens"]
        stats["memex_token_limit"] = measurement["limit"]
        stats["memex_token_encoding"] = measurement["encoding"]
        stats["memex_over_budget"] = bool(measurement["over_budget"])
        if not memex_body.strip() and not allow_empty_memex:
            issues.append("current MEMEX empty")
        if measurement["over_budget"]:
            issues.append(
                f"current MEMEX over budget: {measurement['tokens']}/"
                f"{measurement['limit']} tokens ({measurement['encoding']})"
            )

    return {"valid": not issues, "issues": issues, "stats": stats}
