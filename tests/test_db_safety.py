from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import syke.db_safety as db_safety
from syke.config import user_control_dir
from syke.control import get_receipt, receipt_path, write_receipt
from syke.db import SykeDB
from syke.db_access import DatabaseLeaseUnavailable, acquire_database_lease
from syke.db_safety import (
    capture_baseline,
    create_recovery_point,
    restore_recovery_point,
    rotate_recovery_points,
    validate_state_after_cycle,
)
from syke.memory.learned import LEARNED_PROJECTION_TOKEN_LIMIT, update_learned_memory
from syke.memory.memex import update_memex
from syke.memory.memex_budget import strip_memex_header


def _seed_memory(db: SykeDB, user_id: str, memory_id: str, content: str) -> None:
    db.conn.execute(
        """INSERT INTO memories
           (id, user_id, content, created_at, updated_at)
           VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', NULL)""",
        (memory_id, user_id, content),
    )
    db.conn.commit()


def _corrupt_search_index(db: SykeDB) -> None:
    row = db.conn.execute("SELECT id FROM memories_fts_data ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    db.conn.execute(
        "UPDATE memories_fts_data SET block = zeroblob(4) WHERE id = ?",
        (row["id"],),
    )
    db.conn.commit()
    check = db.conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert db_safety.is_search_index_integrity_issue(check)


def test_graph_recovery_does_not_rewind_protected_host_receipt(tmp_path, user_id: str) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = "cycle-recovery"
        write_receipt(
            user_control_dir(user_id),
            {
                "id": cycle_id,
                "started_at": "2026-08-02T00:00:00+00:00",
                "completed_at": "2026-08-02T00:00:01+00:00",
                "status": "completed",
                "session_id": None,
                "acknowledged_record_ids": [],
                "memex_updated": False,
                "state_change": None,
            },
        )
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-recovery",
            cycle_id=cycle_id,
        )
        assert point.method in {"copy_on_write_clone", "sqlite_backup_fallback"}
        assert point.size_bytes == Path(point.backup_path).stat().st_size
        db.conn.execute("UPDATE memories SET content = 'damaged' WHERE id = 'mem-a'")
        db.conn.commit()

    restore = restore_recovery_point(point)
    assert restore["restored"] is True
    assert restore["integrity_check"] == "ok"

    with SykeDB(db_path, user_id=user_id) as restored:
        row = restored.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "original memory"
        assert get_receipt(user_control_dir(user_id), cycle_id)["status"] == "completed"


def test_recovery_point_rebuilds_malformed_search_index_before_copy(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "searchable quantum memory")
        _corrupt_search_index(db)

        point = create_recovery_point(
            db,
            user_id,
            run_id="run-rebuild-search-before-copy",
            cycle_id=None,
        )

        live_check = db.conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert live_check == "ok"

    with sqlite3.connect(point.backup_path) as backup:
        backup_check = backup.execute("PRAGMA integrity_check").fetchone()[0]
        assert backup_check == "ok"

    manifest = json.loads(Path(point.manifest_path).read_text(encoding="utf-8"))
    assert manifest["search_index_rebuilt"] is True


def test_semantic_gate_accepts_graph_changes_and_current_deletions(
    db,
    user_id: str,
) -> None:
    db.bind_identity(user_id)
    memex_id = update_memex(db, user_id, "canonical memex")
    _seed_memory(db, user_id, "mem-a", "memory a")
    _seed_memory(db, user_id, "mem-b", "memory b")
    _seed_memory(db, user_id, "mem-c", "memory c")
    _seed_memory(db, user_id, "mem-delete", "remove this current memory")
    db.conn.execute(
        """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
           VALUES ('link-revised', ?, 'mem-a', 'mem-b', 'old reason',
                   '2026-01-01T00:00:00+00:00')""",
        (user_id,),
    )
    db.conn.execute(
        """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
           VALUES ('link-removed', ?, 'mem-b', 'mem-c', 'temporary relation',
                   '2026-01-01T00:00:00+00:00')""",
        (user_id,),
    )
    db.conn.commit()
    baseline = capture_baseline(db, user_id)

    assert update_memex(db, user_id, "revised canonical memex") == memex_id
    db.conn.execute("UPDATE memories SET content = 'memory a revised' WHERE id = 'mem-a'")
    _seed_memory(db, user_id, "mem-d", "memory d")
    db.conn.execute("UPDATE links SET reason = 'new reason' WHERE id = 'link-revised'")
    db.conn.execute("DELETE FROM links WHERE id = 'link-removed'")
    db.conn.execute("DELETE FROM memories WHERE id = 'mem-delete'")
    db.conn.execute(
        """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
           VALUES ('link-created', ?, 'mem-c', 'mem-d', 'new relation',
                   '2026-01-02T00:00:00+00:00')""",
        (user_id,),
    )
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)

    assert result["valid"] is True
    assert result["stats"]["memories_created"] == 1
    assert result["stats"]["memories_updated"] == 1
    assert result["stats"]["removed_memory_ids"] == ["mem-delete"]
    current_memex = db.get_memex(user_id)
    assert current_memex is not None
    assert current_memex["id"] == memex_id
    assert current_memex["content"] == "revised canonical memex"
    assert "created_or_revised_without_sources" not in result["stats"]
    assert result["stats"]["graph_change"] == {
        "created_memory_ids": ["mem-d"],
        "revised_memory_ids": ["mem-a"],
        "created_link_ids": ["link-created"],
        "revised_link_ids": ["link-revised"],
        "removed_link_ids": ["link-removed"],
    }


def test_semantic_gate_rejects_rows_outside_the_bound_identity(
    db,
    user_id: str,
) -> None:
    db.bind_identity(user_id)
    update_memex(db, user_id, "canonical memex")
    baseline = capture_baseline(db, user_id)

    db.conn.execute("DROP TRIGGER enforce_memories_identity_insert")
    db.conn.execute(
        """INSERT INTO memories (id, user_id, content, created_at, updated_at)
           VALUES ('foreign-memory', 'other-user', 'wrong owner',
                   '2026-01-01T00:00:00+00:00', NULL)"""
    )
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)

    assert result["valid"] is False
    assert result["stats"]["rows_outside_identity"]["memories"] == 1
    assert any("rows exist outside" in issue for issue in result["issues"])


def test_semantic_gate_rejects_exact_schema_drift(db, user_id: str) -> None:
    db.bind_identity(user_id)
    update_memex(db, user_id, "canonical memex")
    baseline = capture_baseline(db, user_id)
    db.conn.execute("CREATE TABLE graph_history (id TEXT PRIMARY KEY, payload TEXT)")
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)

    assert result["valid"] is False
    assert result["stats"]["schema_valid"] is False
    assert any("current graph schema changed" in issue for issue in result["issues"])


@pytest.mark.parametrize("scenario", ["first", "missing", "over_budget"])
def test_semantic_gate_memex_transitions(db, user_id: str, scenario: str) -> None:
    db.bind_identity(user_id)
    if scenario != "first":
        update_memex(db, user_id, "canonical memex")
    if scenario == "over_budget":
        _seed_memory(db, user_id, "mem-a", "durable memory that must survive")
    baseline = capture_baseline(db, user_id)

    if scenario == "first":
        update_memex(db, user_id, "first memex")
    elif scenario == "missing":
        db.conn.execute("DROP TRIGGER protect_current_memex_delete")
        db.conn.execute("DELETE FROM current_memex")
        db.conn.commit()
    else:
        update_memex(db, user_id, " x" * 2_001)
    result = validate_state_after_cycle(db, user_id, baseline)

    if scenario == "first":
        assert result["valid"] is True
        assert result["stats"]["new_memex_rows"] == 1
    elif scenario == "missing":
        assert result["valid"] is False
        assert any("pre-existing current MEMEX missing" in issue for issue in result["issues"])
    else:
        assert result["valid"] is False
        assert result["stats"]["memex_over_budget"] is True
        assert result["stats"]["memex_tokens"] == 2_001
        assert any("current MEMEX over budget" in issue for issue in result["issues"])
        assert db.count_memories(user_id) == 1


@pytest.mark.parametrize(
    ("before", "after", "expected_issue"),
    [
        (None, " x" * 2_001, "memory mem-a over budget: 2001/2000 tokens"),
        (" x" * 10, " x" * 2_001, "memory mem-a over budget: 2001/2000 tokens"),
        (" x" * 3_000, " x" * 2_500, None),
        (" x" * 3_000, " x" * 3_001, "memory mem-a grew from 3000 to 3001 tokens"),
    ],
    ids=["created-over", "revised-past-budget", "oversized-shrinks", "oversized-grows"],
)
def test_semantic_gate_enforces_per_memory_budget(
    db, user_id: str, before: str | None, after: str, expected_issue: str | None
) -> None:
    db.bind_identity(user_id)
    update_memex(db, user_id, "canonical memex")
    if before is not None:
        _seed_memory(db, user_id, "mem-a", before)
    baseline = capture_baseline(db, user_id)
    if before is None:
        _seed_memory(db, user_id, "mem-a", after)
    else:
        db.conn.execute("UPDATE memories SET content = ? WHERE id = 'mem-a'", (after,))
        db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)

    if expected_issue is None:
        assert result["valid"] is True
        assert result["stats"]["memories_over_budget"] == []
    else:
        assert result["valid"] is False
        assert any(expected_issue in issue for issue in result["issues"])


def test_rotate_recovery_points_keeps_only_newest_automatic_bundle(
    user_id: str,
) -> None:
    recovery_dir = user_control_dir(user_id) / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    automatic_ids = ("automatic-old", "automatic-middle", "automatic-new")

    for recovery_id in automatic_ids:
        (recovery_dir / f"{recovery_id}.json").write_text("{}", encoding="utf-8")
        (recovery_dir / f"{recovery_id}.sqlite").write_bytes(b"database")
        (recovery_dir / f"{recovery_id}.sqlite-wal").write_bytes(b"wal")
        (recovery_dir / f"{recovery_id}.sqlite-shm").write_bytes(b"shm")

    pinned = recovery_dir / "manual-pre-migration.sqlite"
    pinned.write_bytes(b"pinned database")
    Path(f"{pinned}-wal").write_bytes(b"pinned wal")
    Path(f"{pinned}-shm").write_bytes(b"pinned shm")
    (recovery_dir / "orphan-no-manifest.sqlite").write_bytes(b"orphan")
    (recovery_dir / ".interrupted.sqlite.tmp").write_bytes(b"partial")
    (recovery_dir / "README.txt").write_text("not a recovery artifact", encoding="utf-8")

    rotate_recovery_points(user_id, keep_id="automatic-new")

    assert sorted(path.name for path in recovery_dir.iterdir()) == [
        "README.txt",
        "automatic-new.json",
        "automatic-new.sqlite",
    ]


def test_rotate_recovery_points_refuses_to_prune_without_complete_kept_bundle(
    user_id: str,
) -> None:
    recovery_dir = user_control_dir(user_id) / "recovery"
    recovery_dir.mkdir(parents=True, exist_ok=True)
    orphan = recovery_dir / "orphan.sqlite"
    orphan.write_bytes(b"must survive failed reconciliation")

    with pytest.raises(FileNotFoundError, match="complete recovery bundle"):
        rotate_recovery_points(user_id, keep_id="missing")

    assert orphan.exists()


def test_recovery_manifest_contains_only_operational_recovery_facts(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "sensitive original memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-redacted-manifest",
            cycle_id=None,
        )

    manifest = json.loads(Path(point.manifest_path).read_text(encoding="utf-8"))
    assert set(manifest) == {
        "backup_checks",
        "recovery_point",
        "search_index_rebuilt",
        "source_checks",
    }
    encoded = json.dumps(manifest, sort_keys=True)
    assert "sensitive original memory" not in encoded
    assert "mem-a" not in encoded


def test_in_progress_marker_is_written_only_for_a_verified_recovery_pair(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-marker",
            cycle_id="cycle-marker",
        )
    rotate_recovery_points(user_id, keep_id=point.id)

    marker_path = db_safety.mark_recovery_in_progress(
        point,
        started_at="2026-08-10T10:00:00+00:00",
    )

    marker = db_safety.load_recovery_in_progress(user_id)
    assert marker is not None
    assert marker.cycle_id == "cycle-marker"
    assert marker.recovery_point_id == "run-marker"
    assert marker.started_at == "2026-08-10T10:00:00+00:00"
    assert marker_path == user_control_dir(user_id) / "synthesis-in-progress.json"

    assert (
        db_safety.clear_recovery_in_progress(
            user_id,
            cycle_id="cycle-marker",
        )
        is True
    )
    assert db_safety.load_recovery_in_progress(user_id) is None

    Path(point.backup_path).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="size mismatch"):
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
    assert not marker_path.exists()


def test_interrupted_reconciliation_restores_db_and_only_the_memex_projection(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memex_path = workspace / "MEMEX.md"
    unrelated_path = workspace / "scratch.txt"

    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-interrupted",
            cycle_id="cycle-interrupted",
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )

        db.conn.execute("UPDATE memories SET content = 'rejected memory' WHERE id = 'mem-a'")
        db.conn.commit()
        update_memex(db, user_id, "rejected memex")

    memex_path.write_text("rejected projection\n", encoding="utf-8")
    unrelated_path.write_text("attempt residue\n", encoding="utf-8")

    result = db_safety.try_reconcile_before_database_use(
        user_id,
        memex_path=memex_path,
        expected_db_path=db_path,
        completed_at_override="2026-08-10T10:05:00+00:00",
    )

    assert result["action"] == "restored"
    with SykeDB(db_path, user_id=user_id) as restored:
        assert (
            restored.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()[
                "content"
            ]
            == "accepted memory"
        )
        assert restored.get_memex(user_id)["content"] == "accepted memex"
    assert strip_memex_header(memex_path.read_text(encoding="utf-8")).strip() == ("accepted memex")
    assert unrelated_path.read_text(encoding="utf-8") == "attempt residue\n"
    assert db_safety.load_recovery_in_progress(user_id) is None

    receipt = get_receipt(user_control_dir(user_id), "cycle-interrupted")
    assert receipt == {
        "id": "cycle-interrupted",
        "started_at": "2026-08-10T10:00:00+00:00",
        "completed_at": "2026-08-10T10:05:00+00:00",
        "status": "incomplete",
        "session_id": None,
        "acknowledged_record_ids": [],
        "memex_updated": False,
        "recovery": {
            "restored": True,
            "recovery_point": "run-interrupted",
        },
        "error": "Recovered an interrupted synthesis before database use",
    }


def test_interrupted_reconciliation_reuses_caller_owned_exclusive_lease(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    memex_path = tmp_path / "MEMEX.md"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-leased-reconciliation",
            cycle_id="cycle-leased-reconciliation",
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
        db.conn.execute("UPDATE memories SET content = 'interrupted memory' WHERE id = 'mem-a'")
        db.conn.commit()

    lease = acquire_database_lease(db_path, exclusive=True, blocking=False)
    try:
        result = db_safety.reconcile_interrupted_synthesis(
            user_id,
            memex_path=memex_path,
            expected_db_path=db_path,
            exclusive_lease=lease,
        )
        assert result["action"] == "restored"
        assert lease.released is False
        with pytest.raises(DatabaseLeaseUnavailable):
            with acquire_database_lease(db_path, blocking=False):
                pass
    finally:
        lease.release()

    with SykeDB(db_path, user_id=user_id) as restored:
        row = restored.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "accepted memory"
    assert strip_memex_header(memex_path.read_text(encoding="utf-8")).strip() == "accepted memex"
    assert db_safety.load_recovery_in_progress(user_id) is None


@pytest.mark.parametrize(
    ("candidate", "expected", "preserved"),
    [
        (
            "committed learned language after reflection",
            "committed learned language after reflection",
            True,
        ),
        (" x" * LEARNED_PROJECTION_TOKEN_LIMIT, "accepted operating language", False),
    ],
)
def test_interrupted_reconciliation_preserves_only_valid_learned_memory(
    tmp_path,
    user_id: str,
    candidate: str,
    expected: str,
    preserved: bool,
) -> None:
    db_path = tmp_path / "syke.db"
    memex_path = tmp_path / "MEMEX.md"
    cycle_id = "cycle-learned-valid" if preserved else "cycle-learned-oversized"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        update_learned_memory(db, user_id, "accepted operating language")
        point = create_recovery_point(
            db,
            user_id,
            run_id=f"run-{cycle_id}",
            cycle_id=cycle_id,
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
        db.conn.execute("UPDATE memories SET content = 'rejected memory' WHERE id = 'mem-a'")
        db.conn.commit()
        update_learned_memory(db, user_id, candidate)

    result = db_safety.reconcile_interrupted_synthesis(
        user_id,
        memex_path=memex_path,
        expected_db_path=db_path,
    )

    assert result["action"] == "restored"
    assert result["recovery"].get("learned_memory_preserved", False) is preserved
    with SykeDB(db_path, user_id=user_id) as restored:
        ordinary = restored.conn.execute(
            "SELECT content FROM memories WHERE id = 'mem-a'"
        ).fetchone()
        learned = restored.conn.execute(
            "SELECT content FROM memories WHERE id = 'syke-learned'"
        ).fetchone()
        learned_fts = restored.conn.execute(
            "SELECT content FROM memories_fts WHERE memory_id = 'syke-learned'"
        ).fetchone()
        assert ordinary["content"] == "accepted memory"
        assert learned["content"] == expected
        assert learned_fts["content"] == learned["content"]
    receipt = get_receipt(user_control_dir(user_id), cycle_id)
    assert receipt is not None
    receipt_recovery = receipt.get("recovery")
    assert isinstance(receipt_recovery, dict)
    assert receipt_recovery.get("learned_memory_preserved", False) is preserved


def test_reconciliation_does_not_restore_while_synthesis_lock_is_active(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    memex_path = tmp_path / "MEMEX.md"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-active",
            cycle_id="cycle-active",
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
        db.conn.execute("UPDATE memories SET content = 'live attempt' WHERE id = 'mem-a'")
        db.conn.commit()

    lock_handle, _ = db_safety.acquire_synthesis_lock(user_id)
    try:
        result = db_safety.try_reconcile_before_database_use(
            user_id,
            memex_path=memex_path,
            expected_db_path=db_path,
        )
    finally:
        db_safety.release_synthesis_lock(lock_handle)

    assert result == {"action": "active", "cycle_id": "cycle-active"}
    with SykeDB(db_path, user_id=user_id) as current:
        row = current.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "live attempt"
    assert db_safety.load_recovery_in_progress(user_id) is not None
    assert get_receipt(user_control_dir(user_id), "cycle-active") is None


def test_reconciliation_refuses_to_open_unrestored_state_behind_an_unlocked_reader(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    memex_path = tmp_path / "MEMEX.md"
    db = SykeDB(db_path, user_id=user_id)
    try:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-busy-reader",
            cycle_id="cycle-busy-reader",
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
        db.conn.execute("UPDATE memories SET content = 'unrestored' WHERE id = 'mem-a'")
        db.conn.commit()

        with pytest.raises(RuntimeError, match="database users are active"):
            db_safety.try_reconcile_before_database_use(
                user_id,
                memex_path=memex_path,
                expected_db_path=db_path,
            )

        assert db_safety.load_recovery_in_progress(user_id) is not None
        assert get_receipt(user_control_dir(user_id), "cycle-busy-reader") is None
    finally:
        db.close()


@pytest.mark.parametrize("final_status", ["completed", "failed"])
def test_reconciliation_never_restores_over_any_final_receipt(
    tmp_path,
    user_id: str,
    final_status: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-final",
            cycle_id="cycle-final",
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
        db.conn.execute("UPDATE memories SET content = 'accepted current' WHERE id = 'mem-a'")
        db.conn.commit()

    write_receipt(
        user_control_dir(user_id),
        {
            "id": "cycle-final",
            "started_at": "2026-08-10T10:00:00+00:00",
            "completed_at": "2026-08-10T10:01:00+00:00",
            "status": final_status,
            "session_id": None,
            "acknowledged_record_ids": [],
            "memex_updated": False,
        },
    )

    result = db_safety.reconcile_interrupted_synthesis(
        user_id,
        memex_path=tmp_path / "MEMEX.md",
        expected_db_path=db_path,
    )

    assert result == {"action": "finalized", "cycle_id": "cycle-final"}
    with SykeDB(db_path, user_id=user_id) as current:
        assert (
            current.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()[
                "content"
            ]
            == "accepted current"
        )
    assert db_safety.load_recovery_in_progress(user_id) is None


def test_reconciliation_refuses_to_restore_over_an_unreadable_receipt(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-unreadable",
            cycle_id="cycle-unreadable",
        )
        db_safety.mark_recovery_in_progress(
            point,
            started_at="2026-08-10T10:00:00+00:00",
        )
        db.conn.execute("UPDATE memories SET content = 'accepted current' WHERE id = 'mem-a'")
        db.conn.commit()

    broken = receipt_path(user_control_dir(user_id), "cycle-unreadable")
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="unreadable"):
        db_safety.reconcile_interrupted_synthesis(
            user_id,
            memex_path=tmp_path / "MEMEX.md",
            expected_db_path=db_path,
        )

    with SykeDB(db_path, user_id=user_id) as current:
        assert (
            current.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()[
                "content"
            ]
            == "accepted current"
        )
    assert db_safety.load_recovery_in_progress(user_id) is not None


def test_reconciliation_without_a_marker_does_not_restore_a_recovery_copy(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-unmarked",
            cycle_id="cycle-unmarked",
        )
        rotate_recovery_points(user_id, keep_id=point.id)
        db.conn.execute("UPDATE memories SET content = 'current memory' WHERE id = 'mem-a'")
        db.conn.commit()

    assert db_safety.try_reconcile_before_database_use(
        user_id,
        memex_path=tmp_path / "MEMEX.md",
        expected_db_path=db_path,
    ) == {"action": "none"}
    with SykeDB(db_path, user_id=user_id) as current:
        assert (
            current.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()[
                "content"
            ]
            == "current memory"
        )


def test_restore_refuses_damaged_recovery_point_without_replacing_db(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = "cycle-damaged-recovery"
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-damaged-recovery",
            cycle_id=cycle_id,
        )
        db.conn.execute("UPDATE memories SET content = 'current live state' WHERE id = 'mem-a'")
        db.conn.commit()

    Path(point.backup_path).write_bytes(b"not a sqlite db")

    with pytest.raises(ValueError, match="size mismatch"):
        restore_recovery_point(point)

    with SykeDB(db_path, user_id=user_id) as db:
        row = db.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "current live state"


def test_restore_refuses_while_a_normal_database_user_is_open(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        _seed_memory(db, user_id, "mem-a", "accepted memory")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-contended-restore",
            cycle_id="cycle-contended-restore",
        )
        db.conn.execute("UPDATE memories SET content = 'live memory' WHERE id = 'mem-a'")
        db.conn.commit()

        with pytest.raises(RuntimeError, match="active database users"):
            restore_recovery_point(point)

        row = db.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "live memory"

    with SykeDB(db_path, user_id=user_id) as current:
        row = current.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "live memory"


def test_restore_keeps_later_cross_process_writes_visible(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "accepted memex")
        update_learned_memory(db, user_id, "accepted operating language")
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-cross-process-restore",
            cycle_id="cycle-cross-process-restore",
        )
        update_learned_memory(db, user_id, "preserved operating language")
        learned_snapshot = dict(
            db.conn.execute("SELECT * FROM memories WHERE id = 'syke-learned'").fetchone()
        )

    restore_recovery_point(point, learned_snapshot=learned_snapshot)

    with SykeDB(db_path, user_id=user_id) as restored:
        script = """
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute(
        \"UPDATE memories SET content = ?, updated_at = ? WHERE id = 'syke-learned'\",
        (\"later operating language\", \"2026-08-15T09:00:00+00:00\"),
    )
    row = conn.execute(
        \"SELECT content FROM memories WHERE id = 'syke-learned'\"
    ).fetchone()
    print(row[0])
"""
        writer = subprocess.run(
            [sys.executable, "-c", script, str(db_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert writer.stdout.strip() == "later operating language"

        row = restored.conn.execute(
            "SELECT content FROM memories WHERE id = 'syke-learned'"
        ).fetchone()
        assert row["content"] == "later operating language"

        checkpoint = restored.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert checkpoint[0] == 0

    with SykeDB(db_path, user_id=user_id) as reopened:
        row = reopened.conn.execute(
            "SELECT content FROM memories WHERE id = 'syke-learned'"
        ).fetchone()
        assert row["content"] == "later operating language"


def test_recovery_point_refuses_large_full_copy_fallback(
    tmp_path,
    user_id: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(db_safety, "_try_copy_on_write_clone", lambda source, destination: False)

    db_path = tmp_path / "syke.db"
    with SykeDB(db_path, user_id=user_id) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = "cycle-refuse-copy"
        with pytest.raises(RuntimeError, match="refusing full-copy fallback"):
            create_recovery_point(
                db,
                user_id,
                run_id="run-refuse-copy",
                cycle_id=cycle_id,
                max_full_copy_fallback_bytes=1,
            )
