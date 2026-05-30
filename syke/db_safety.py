"""Recovery points and deterministic gates for the single Syke DB."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.config import user_data_dir

MEMEX_MARKER_SQL = '["__memex__"]'
REQUIRED_TABLES = {"memories", "links", "cycle_records", "rollout_traces"}
MAX_FULL_COPY_FALLBACK_BYTES = 64 * 1024 * 1024


@dataclass
class MemoryFingerprint:
    id: str
    content_hash: str
    active: int
    superseded_by: str | None
    updated_at: str | None


@dataclass
class StateBaseline:
    user_id: str
    captured_at: str
    active_non_memex_count: int
    memories_total: int
    links_total: int
    active_memories: dict[str, MemoryFingerprint] = field(default_factory=dict)
    memex_memories: dict[str, MemoryFingerprint] = field(default_factory=dict)
    memex_id: str | None = None
    memex_hash: str | None = None
    memex_count: int = 0
    table_counts: dict[str, int] = field(default_factory=dict)


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


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _hash_text(value: Any) -> str:
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()


def _strip_memex_header(content: str) -> str:
    lines = content.split("\n")
    if lines and lines[0].startswith("# MEMEX ["):
        return "\n".join(lines[1:]).lstrip("\n")
    return content


def _recovery_dir(user_id: str) -> Path:
    path = user_data_dir(user_id) / "recovery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def capture_baseline(db: Any, user_id: str) -> StateBaseline:
    """Capture the pre-agent semantic shape used by the post-cycle gate."""
    rows = db.conn.execute(
        """SELECT id, content, active, superseded_by, updated_at
           FROM memories
           WHERE user_id = ?
             AND active = 1
             AND (source_event_ids IS NULL OR source_event_ids != ?)""",
        (user_id, MEMEX_MARKER_SQL),
    ).fetchall()
    active = {
        str(row["id"]): MemoryFingerprint(
            id=str(row["id"]),
            content_hash=_hash_text(row["content"]),
            active=int(row["active"] or 0),
            superseded_by=_text(row["superseded_by"]) or None,
            updated_at=_text(row["updated_at"]) or None,
        )
        for row in rows
    }

    active_memex_rows = db.conn.execute(
        """SELECT id, content
           FROM memories
           WHERE user_id = ? AND active = 1 AND source_event_ids = ?
           ORDER BY datetime(created_at) DESC, id DESC""",
        (user_id, MEMEX_MARKER_SQL),
    ).fetchall()
    memex_content = (
        _strip_memex_header(_text(active_memex_rows[0]["content"])) if active_memex_rows else ""
    )

    memex_rows = db.conn.execute(
        """SELECT id, content, active, superseded_by, updated_at
           FROM memories
           WHERE user_id = ? AND source_event_ids = ?
           ORDER BY datetime(created_at) DESC, id DESC""",
        (user_id, MEMEX_MARKER_SQL),
    ).fetchall()
    memex_memories = {
        str(row["id"]): MemoryFingerprint(
            id=str(row["id"]),
            content_hash=_hash_text(_strip_memex_header(_text(row["content"]))),
            active=int(row["active"] or 0),
            superseded_by=_text(row["superseded_by"]) or None,
            updated_at=_text(row["updated_at"]) or None,
        )
        for row in memex_rows
    }

    table_counts: dict[str, int] = {}
    for table in ("memories", "links", "cycle_records", "rollout_traces"):
        table_counts[table] = int(db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    return StateBaseline(
        user_id=user_id,
        captured_at=datetime.now(UTC).isoformat(),
        active_non_memex_count=len(active),
        memories_total=table_counts["memories"],
        links_total=table_counts["links"],
        active_memories=active,
        memex_memories=memex_memories,
        memex_id=str(active_memex_rows[0]["id"]) if active_memex_rows else None,
        memex_hash=_hash_text(memex_content) if memex_content.strip() else None,
        memex_count=len(active_memex_rows),
        table_counts=table_counts,
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _json_ready(asdict(value))
    return value


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
    except OSError:
        _unlink_if_exists(destination)
        return False
    if completed.returncode != 0 or not destination.exists():
        _unlink_if_exists(destination)
        return False
    return True


def _sqlite_backup_copy(db: Any, destination: Path) -> None:
    _unlink_if_exists(destination)
    with sqlite3.connect(str(destination)) as backup_conn:
        db.conn.backup(backup_conn)


def _sqlite_checks(path: Path) -> dict[str, str | None]:
    with sqlite3.connect(str(path)) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        quick = conn.execute("PRAGMA quick_check").fetchone()
    return {
        "integrity_check": integrity[0] if integrity else None,
        "quick_check": quick[0] if quick else None,
    }


def _require_sqlite_ok(path: Path, label: str) -> dict[str, str | None]:
    checks = _sqlite_checks(path)
    if checks["integrity_check"] != "ok" or checks["quick_check"] != "ok":
        raise ValueError(
            f"{label} failed SQLite checks: "
            f"integrity_check={checks['integrity_check']}, quick_check={checks['quick_check']}"
        )
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
    baseline: StateBaseline,
    max_full_copy_fallback_bytes: int = MAX_FULL_COPY_FALLBACK_BYTES,
) -> RecoveryPoint:
    """Create a cheap local recovery copy for the current cycle."""
    db_path = Path(str(db.db_path)).expanduser().resolve()
    if str(db.db_path) == ":memory:":
        raise ValueError("Recovery points require a file-backed SQLite database")
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))

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
    os.replace(tmp_backup_path, backup_path)
    size_bytes = backup_path.stat().st_size

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
        "baseline": _json_ready(baseline),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return point


def rotate_recovery_points(user_id: str, *, keep: int = 8) -> None:
    """Keep a small number of newest recovery point pairs."""
    recovery_dir = _recovery_dir(user_id)
    manifests = sorted(recovery_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for manifest in manifests[keep:]:
        stem = manifest.stem
        for candidate in (manifest, recovery_dir / f"{stem}.sqlite"):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def restore_recovery_point(point: RecoveryPoint) -> dict[str, Any]:
    """Restore the database file from a recovery point."""
    backup_path = Path(point.backup_path)
    db_path = Path(point.db_path)
    if not backup_path.exists():
        raise FileNotFoundError(str(backup_path))
    actual_size = backup_path.stat().st_size
    if actual_size != point.size_bytes:
        raise ValueError("Recovery point size mismatch")
    backup_checks = _require_sqlite_ok(backup_path, "Recovery point")

    tmp_path = db_path.with_name(f"{db_path.name}.restore-tmp")
    _unlink_if_exists(tmp_path)
    if not _try_copy_on_write_clone(backup_path, tmp_path):
        shutil.copy2(backup_path, tmp_path)
    for suffix in ("-wal", "-shm"):
        _unlink_if_exists(Path(f"{db_path}{suffix}"))
    os.replace(tmp_path, db_path)

    restored_checks = _require_sqlite_ok(db_path, "Restored database")
    return {
        "restored": True,
        "recovery_point": point.id,
        "integrity_check": restored_checks["integrity_check"],
        "quick_check": restored_checks["quick_check"],
        "backup_integrity_check": backup_checks["integrity_check"],
        "backup_quick_check": backup_checks["quick_check"],
    }


def _collapse_tolerance(count: int) -> int:
    return max(2, count // 10)


def _bulk_change_tolerance(count: int) -> int:
    return max(3, count // 10)


def _plain_deactivation_tolerance(count: int) -> int:
    return max(1, count // 50)


def validate_state_after_cycle(
    db: Any,
    user_id: str,
    baseline: StateBaseline,
    *,
    allow_empty_memex: bool = False,
    memex_token_limit: int = 2000,
    chars_per_token: int = 4,
) -> dict[str, Any]:
    """Validate that agent writes did not collapse the semantic store."""
    issues: list[str] = []
    stats: dict[str, Any] = {
        "baseline_active_non_memex": baseline.active_non_memex_count,
        "baseline_memex_count": baseline.memex_count,
    }

    table_rows = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {str(row["name"]) for row in table_rows}
    missing_tables = sorted(REQUIRED_TABLES - tables)
    stats["tables"] = sorted(tables)
    if missing_tables:
        issues.append(f"missing required tables: {', '.join(missing_tables)}")

    try:
        integrity = db.conn.execute("PRAGMA integrity_check").fetchone()
        quick = db.conn.execute("PRAGMA quick_check").fetchone()
        stats["integrity_check"] = integrity[0] if integrity else None
        stats["quick_check"] = quick[0] if quick else None
        if stats["integrity_check"] != "ok":
            issues.append(f"integrity_check failed: {stats['integrity_check']}")
        if stats["quick_check"] != "ok":
            issues.append(f"quick_check failed: {stats['quick_check']}")
    except sqlite3.Error as exc:
        issues.append(f"database validation error: {exc}")
        return {"valid": False, "issues": issues, "stats": stats}

    if missing_tables:
        return {"valid": False, "issues": issues, "stats": stats}

    active_count = int(
        db.conn.execute(
            """SELECT COUNT(*)
               FROM memories
               WHERE user_id = ?
                 AND active = 1
                 AND (source_event_ids IS NULL OR source_event_ids != ?)""",
            (user_id, MEMEX_MARKER_SQL),
        ).fetchone()[0]
    )
    stats["active_non_memex"] = active_count
    allowed_drop = _collapse_tolerance(baseline.active_non_memex_count)
    if active_count < baseline.active_non_memex_count - allowed_drop:
        issues.append(
            "active non-MEMEX memories collapsed: "
            f"{baseline.active_non_memex_count} -> {active_count}"
        )

    current_rows = db.conn.execute(
        """SELECT id, content, active, superseded_by, updated_at
           FROM memories
           WHERE user_id = ?
             AND id IN ({})""".format(",".join("?" for _ in baseline.active_memories) or "NULL"),
        (user_id, *baseline.active_memories.keys()),
    ).fetchall()
    current = {str(row["id"]): row for row in current_rows}

    missing_ids: list[str] = []
    overwritten_ids: list[str] = []
    superseded_ids: list[str] = []
    deactivated_ids: list[str] = []
    for memory_id, before in baseline.active_memories.items():
        row = current.get(memory_id)
        if row is None:
            missing_ids.append(memory_id)
            continue
        row_active = int(row["active"] or 0)
        if row_active == 1 and _hash_text(row["content"]) != before.content_hash:
            overwritten_ids.append(memory_id)
            continue
        if row_active == 0:
            successor = _text(row["superseded_by"]) or None
            if successor:
                target = db.conn.execute(
                    "SELECT 1 FROM memories WHERE user_id = ? AND id = ? LIMIT 1",
                    (user_id, successor),
                ).fetchone()
                if target:
                    superseded_ids.append(memory_id)
                else:
                    deactivated_ids.append(memory_id)
            else:
                deactivated_ids.append(memory_id)

    stats["missing_baseline_active"] = len(missing_ids)
    stats["overwritten_baseline_active"] = len(overwritten_ids)
    stats["superseded_baseline_active"] = len(superseded_ids)
    stats["deactivated_baseline_active"] = len(deactivated_ids)
    if missing_ids:
        issues.append(f"pre-existing active memories deleted: {len(missing_ids)}")
    if overwritten_ids:
        issues.append(f"pre-existing active memories overwritten in place: {len(overwritten_ids)}")
    if len(superseded_ids) > _bulk_change_tolerance(baseline.active_non_memex_count):
        issues.append(f"too many pre-existing memories revised at once: {len(superseded_ids)}")
    if len(deactivated_ids) > _plain_deactivation_tolerance(baseline.active_non_memex_count):
        issues.append(
            "too many pre-existing memories deactivated without replacement: "
            f"{len(deactivated_ids)}"
        )

    baseline_memex_ids = list(baseline.memex_memories.keys())
    if baseline_memex_ids:
        current_memex_history_rows = db.conn.execute(
            """SELECT id, content, source_event_ids
               FROM memories
               WHERE user_id = ?
                 AND id IN ({})""".format(",".join("?" for _ in baseline_memex_ids)),
            (user_id, *baseline_memex_ids),
        ).fetchall()
        current_memex_history = {str(row["id"]): row for row in current_memex_history_rows}

        missing_memex_ids: list[str] = []
        rewritten_memex_ids: list[str] = []
        retagged_memex_ids: list[str] = []
        for memory_id, before in baseline.memex_memories.items():
            row = current_memex_history.get(memory_id)
            if row is None:
                missing_memex_ids.append(memory_id)
                continue
            if _text(row["source_event_ids"]) != MEMEX_MARKER_SQL:
                retagged_memex_ids.append(memory_id)
                continue
            content = _strip_memex_header(_text(row["content"]))
            if _hash_text(content) != before.content_hash:
                rewritten_memex_ids.append(memory_id)

        stats["baseline_memex_rows"] = len(baseline.memex_memories)
        stats["missing_baseline_memex"] = len(missing_memex_ids)
        stats["rewritten_baseline_memex"] = len(rewritten_memex_ids)
        stats["retagged_baseline_memex"] = len(retagged_memex_ids)
        if missing_memex_ids:
            issues.append(f"pre-existing MEMEX rows deleted: {len(missing_memex_ids)}")
        if rewritten_memex_ids:
            issues.append(f"pre-existing MEMEX rows rewritten: {len(rewritten_memex_ids)}")
        if retagged_memex_ids:
            issues.append(
                f"pre-existing MEMEX rows lost canonical marker: {len(retagged_memex_ids)}"
            )

    memex_rows = db.conn.execute(
        """SELECT id, content
           FROM memories
           WHERE user_id = ? AND active = 1 AND source_event_ids = ?
           ORDER BY datetime(created_at) DESC, id DESC""",
        (user_id, MEMEX_MARKER_SQL),
    ).fetchall()
    stats["active_memex_count"] = len(memex_rows)
    if not allow_empty_memex:
        if len(memex_rows) == 0:
            issues.append("canonical MEMEX missing")
        elif len(memex_rows) > 1:
            issues.append(f"duplicate active canonical MEMEX rows: {len(memex_rows)}")
    elif len(memex_rows) > 1:
        issues.append(f"duplicate active canonical MEMEX rows: {len(memex_rows)}")
    if memex_rows:
        memex_body = _strip_memex_header(_text(memex_rows[0]["content"]))
        token_estimate = len(memex_body) // chars_per_token
        stats["memex_tokens"] = token_estimate
        if not memex_body.strip() and not allow_empty_memex:
            issues.append("canonical MEMEX empty")
        if token_estimate > memex_token_limit:
            issues.append(
                f"canonical MEMEX over budget: {token_estimate}/{memex_token_limit} tokens"
            )

    broken_links = int(
        db.conn.execute(
            """SELECT COUNT(*)
               FROM links l
               LEFT JOIN memories s ON s.user_id = l.user_id AND s.id = l.source_id
               LEFT JOIN memories t ON t.user_id = l.user_id AND t.id = l.target_id
               WHERE l.user_id = ? AND (s.id IS NULL OR t.id IS NULL)""",
            (user_id,),
        ).fetchone()[0]
    )
    stats["broken_links"] = broken_links
    if broken_links:
        issues.append(f"links reference missing memories: {broken_links}")

    return {"valid": not issues, "issues": issues, "stats": stats}
