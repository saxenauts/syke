from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import syke.db_safety as db_safety
from syke.db import SykeDB
from syke.db_safety import (
    capture_baseline,
    create_recovery_point,
    restore_recovery_point,
    validate_state_after_cycle,
)
from syke.memory.memex import update_memex
from syke.models import Memory


def _seed_memory(db: SykeDB, user_id: str, memory_id: str, content: str) -> None:
    db.insert_memory(Memory(id=memory_id, user_id=user_id, content=content))


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


@pytest.mark.parametrize(
    "message",
    [
        "malformed inverted index for FTS5 table main.memories_fts",
        'fts5: corruption found reading blob 274877906945 from table "memories_fts"',
    ],
)
def test_search_index_integrity_issue_recognizes_sqlite_fts_variants(message: str) -> None:
    assert db_safety.is_search_index_integrity_issue(message)


def test_search_index_integrity_issue_rejects_other_fts_tables() -> None:
    assert not db_safety.is_search_index_integrity_issue(
        'fts5: corruption found reading blob 274877906945 from table "other_fts"'
    )


def test_recovery_point_restores_db_bytes_and_running_cycle(tmp_path, user_id: str) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        baseline = capture_baseline(db, user_id)
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-recovery",
            cycle_id=cycle_id,
            baseline=baseline,
        )
        assert point.method in {"copy_on_write_clone", "sqlite_backup_fallback"}
        assert point.size_bytes == Path(point.backup_path).stat().st_size
        db.conn.execute("UPDATE memories SET content = 'damaged' WHERE id = 'mem-a'")
        db.conn.commit()

    restore = restore_recovery_point(point)
    assert restore["restored"] is True
    assert restore["integrity_check"] == "ok"

    with SykeDB(db_path) as restored:
        row = restored.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        cycle = restored.conn.execute(
            "SELECT status FROM cycle_records WHERE id = ?", (cycle_id,)
        ).fetchone()
        assert row["content"] == "original memory"
        assert cycle["status"] == "running"


def test_recovery_point_rebuilds_malformed_search_index_before_copy(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "searchable quantum memory")
        baseline = capture_baseline(db, user_id)
        _corrupt_search_index(db)

        point = create_recovery_point(
            db,
            user_id,
            run_id="run-rebuild-search-before-copy",
            cycle_id=None,
            baseline=baseline,
        )

        live_check = db.conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert live_check == "ok"

    with sqlite3.connect(point.backup_path) as backup:
        backup_check = backup.execute("PRAGMA integrity_check").fetchone()[0]
        assert backup_check == "ok"

    manifest = Path(point.manifest_path).read_text(encoding="utf-8")
    assert '"search_index_rebuilt": true' in manifest


def test_semantic_gate_preserves_active_update_and_blocks_bad_links(db, user_id: str) -> None:
    update_memex(db, user_id, "canonical memex")
    _seed_memory(db, user_id, "mem-a", "original memory")
    baseline = capture_baseline(db, user_id)

    db.conn.execute("UPDATE memories SET content = 'overwritten' WHERE id = 'mem-a'")
    db.conn.execute(
        "INSERT INTO links (id, user_id, source_id, target_id, reason, created_at) "
        "VALUES ('link-bad', ?, 'mem-a', 'missing-memory', 'bad target', '2026-01-01T00:00:00Z')",
        (user_id,),
    )
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is False
    assert result["stats"]["normalized_active_memory_updates"] == 1
    old = db.conn.execute("SELECT content, active, superseded_by FROM memories WHERE id = 'mem-a'")
    old_row = old.fetchone()
    assert old_row["content"] == "original memory"
    assert old_row["active"] == 0
    successor = db.conn.execute(
        "SELECT content, active FROM memories WHERE id = ?",
        (old_row["superseded_by"],),
    ).fetchone()
    assert successor["content"] == "overwritten"
    assert successor["active"] == 1
    assert any("links reference missing memories" in issue for issue in result["issues"])


def test_semantic_gate_allows_direct_active_memory_update(db, user_id: str) -> None:
    update_memex(db, user_id, "canonical memex")
    _seed_memory(db, user_id, "mem-a", "original memory")
    baseline = capture_baseline(db, user_id)

    db.conn.execute("UPDATE memories SET content = 'updated memory' WHERE id = 'mem-a'")
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is True
    assert result["stats"]["normalized_active_memory_updates"] == 1
    old = db.conn.execute(
        "SELECT content, active, superseded_by FROM memories WHERE id = 'mem-a'"
    ).fetchone()
    assert old["content"] == "original memory"
    assert old["active"] == 0
    successor = db.conn.execute(
        "SELECT content, active FROM memories WHERE id = ?",
        (old["superseded_by"],),
    ).fetchone()
    assert successor["content"] == "updated memory"
    assert successor["active"] == 1


def test_semantic_gate_rebuilds_malformed_search_index(db, user_id: str) -> None:
    update_memex(db, user_id, "canonical memex")
    _seed_memory(db, user_id, "mem-a", "searchable quantum memory")
    baseline = capture_baseline(db, user_id)
    _corrupt_search_index(db)

    result = validate_state_after_cycle(db, user_id, baseline)

    assert result["valid"] is True
    assert result["stats"]["search_index_rebuilt"] is True
    assert result["stats"]["integrity_check"] == "ok"
    assert result["stats"]["quick_check"] == "ok"
    rows = db.conn.execute(
        """SELECT fts.memory_id
           FROM memories_fts fts
           JOIN memories m ON m.id = fts.memory_id
           WHERE memories_fts MATCH ?
             AND m.user_id = ?
             AND m.active = 1""",
        ("quantum", user_id),
    ).fetchall()
    assert [row["memory_id"] for row in rows] == ["mem-a"]


def test_semantic_gate_allows_memex_revision_when_old_rows_remain(db, user_id: str) -> None:
    old_id = update_memex(db, user_id, "old memex")
    baseline = capture_baseline(db, user_id)

    new_id = update_memex(db, user_id, "new memex")

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is True
    old = db.conn.execute("SELECT content, active FROM memories WHERE id = ?", (old_id,)).fetchone()
    assert old["content"] == "old memex"
    assert old["active"] == 0
    assert new_id != old_id


def test_semantic_gate_blocks_deleted_memex_history(db, user_id: str) -> None:
    old_id = update_memex(db, user_id, "old memex")
    update_memex(db, user_id, "new memex")
    baseline = capture_baseline(db, user_id)

    db.conn.execute("DELETE FROM memories WHERE user_id = ? AND id = ?", (user_id, old_id))
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is False
    assert any("pre-existing MEMEX rows deleted" in issue for issue in result["issues"])


def test_semantic_gate_blocks_rewritten_memex_history(db, user_id: str) -> None:
    old_id = update_memex(db, user_id, "old memex")
    update_memex(db, user_id, "new memex")
    baseline = capture_baseline(db, user_id)

    db.conn.execute(
        "UPDATE memories SET content = ? WHERE user_id = ? AND id = ?",
        ("rewritten old memex", user_id, old_id),
    )
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is False
    assert any("pre-existing MEMEX rows rewritten" in issue for issue in result["issues"])


def test_semantic_gate_blocks_retagged_memex_history(db, user_id: str) -> None:
    old_id = update_memex(db, user_id, "old memex")
    update_memex(db, user_id, "new memex")
    baseline = capture_baseline(db, user_id)

    db.conn.execute(
        "UPDATE memories SET source_event_ids = ? WHERE user_id = ? AND id = ?",
        ("[]", user_id, old_id),
    )
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is False
    assert any(
        "pre-existing MEMEX rows lost canonical marker" in issue for issue in result["issues"]
    )


def test_semantic_gate_blocks_catastrophic_active_memory_collapse(db, user_id: str) -> None:
    update_memex(db, user_id, "canonical memex")
    for index in range(6):
        _seed_memory(db, user_id, f"mem-{index}", f"memory {index}")
    baseline = capture_baseline(db, user_id)

    db.conn.execute(
        "UPDATE memories SET active = 0 WHERE user_id = ? AND source_event_ids != ?",
        (user_id, '["__memex__"]'),
    )
    db.conn.commit()

    result = validate_state_after_cycle(db, user_id, baseline)
    assert result["valid"] is False
    assert any("collapsed" in issue for issue in result["issues"])
    assert any("deactivated without replacement" in issue for issue in result["issues"])


def test_semantic_gate_ignores_old_world_tables_when_present_or_absent(db, user_id: str) -> None:
    update_memex(db, user_id, "canonical memex")
    _seed_memory(db, user_id, "mem-a", "original memory")
    baseline = capture_baseline(db, user_id)

    absent_result = validate_state_after_cycle(db, user_id, baseline)
    assert absent_result["valid"] is True

    db.conn.execute(
        "CREATE TABLE memory_ops (id TEXT, user_id TEXT, operation TEXT, created_at TEXT)"
    )
    db.conn.execute("CREATE TABLE cycle_annotations (id TEXT, user_id TEXT, created_at TEXT)")
    db.conn.commit()

    present_result = validate_state_after_cycle(db, user_id, baseline)
    assert present_result["valid"] is True


def test_recovery_point_uses_cheap_clone_or_small_safe_fallback(tmp_path, user_id: str) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        baseline = capture_baseline(db, user_id)
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-safe-copy",
            cycle_id=cycle_id,
            baseline=baseline,
        )

    with sqlite3.connect(point.backup_path) as backup:
        row = backup.execute("PRAGMA integrity_check").fetchone()
        assert row[0] == "ok"
    assert point.method in {"copy_on_write_clone", "sqlite_backup_fallback"}
    assert point.size_bytes > 0


def test_recovery_manifest_redacts_memory_content(tmp_path, user_id: str) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "sensitive original memory")
        baseline = capture_baseline(db, user_id)
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-redacted-manifest",
            cycle_id=None,
            baseline=baseline,
        )

    manifest = Path(point.manifest_path).read_text(encoding="utf-8")
    assert "sensitive original memory" not in manifest
    assert "<redacted:" in manifest


def test_restore_refuses_damaged_recovery_point_without_replacing_db(
    tmp_path,
    user_id: str,
) -> None:
    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        baseline = capture_baseline(db, user_id)
        point = create_recovery_point(
            db,
            user_id,
            run_id="run-damaged-recovery",
            cycle_id=cycle_id,
            baseline=baseline,
        )
        db.conn.execute("UPDATE memories SET content = 'current live state' WHERE id = 'mem-a'")
        db.conn.commit()

    Path(point.backup_path).write_bytes(b"not a sqlite db")

    with pytest.raises(ValueError, match="size mismatch"):
        restore_recovery_point(point)

    with SykeDB(db_path) as db:
        row = db.conn.execute("SELECT content FROM memories WHERE id = 'mem-a'").fetchone()
        assert row["content"] == "current live state"


def test_recovery_point_refuses_large_full_copy_fallback(
    tmp_path,
    user_id: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(db_safety, "_try_copy_on_write_clone", lambda source, destination: False)

    db_path = tmp_path / "syke.db"
    with SykeDB(db_path) as db:
        update_memex(db, user_id, "canonical memex")
        _seed_memory(db, user_id, "mem-a", "original memory")
        cycle_id = db.insert_cycle_record(user_id, model="pi")
        baseline = capture_baseline(db, user_id)

        with pytest.raises(RuntimeError, match="refusing full-copy fallback"):
            create_recovery_point(
                db,
                user_id,
                run_id="run-refuse-copy",
                cycle_id=cycle_id,
                baseline=baseline,
                max_full_copy_fallback_bytes=1,
            )
