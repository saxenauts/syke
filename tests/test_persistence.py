"""Tests for the persistence layer."""

from __future__ import annotations

from pathlib import Path

from syke.db import SykeDB
from syke.models import Memory


def _memory_row(db: SykeDB, user_id: str, memory_id: str) -> dict | None:
    row = db.conn.execute(
        "SELECT * FROM memories WHERE user_id = ? AND id = ?",
        (user_id, memory_id),
    ).fetchone()
    return dict(row) if row else None


def _search_memory_ids(db: SykeDB, user_id: str, query: str) -> list[str]:
    rows = db.conn.execute(
        """SELECT m.id
           FROM memories_fts fts
           JOIN memories m ON m.id = fts.memory_id
           WHERE memories_fts MATCH ? AND m.user_id = ? AND m.active = 1
           ORDER BY bm25(memories_fts)""",
        (query, user_id),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _linked_memory_ids(db: SykeDB, user_id: str, memory_id: str) -> list[str]:
    rows = db.conn.execute(
        """SELECT m.id
           FROM links l
           JOIN memories m ON (
               (l.source_id = ? AND m.id = l.target_id) OR
               (l.target_id = ? AND m.id = l.source_id)
           )
           WHERE l.user_id = ? AND m.active = 1
           ORDER BY l.created_at DESC""",
        (memory_id, memory_id, user_id),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def test_migration_idempotent(tmp_path: Path):
    db = SykeDB(tmp_path / "idem.db")
    db.initialize()
    db.initialize()
    assert db.count_memories("nobody") == 0
    db.close()


def test_insert_memory_persists_active_row(db, user_id):
    db.insert_memory(Memory(id="m1", user_id=user_id, content="Utkarsh loves AI agents"))
    result = _memory_row(db, user_id, "m1")
    assert result is not None
    assert result["content"] == "Utkarsh loves AI agents"
    assert result["active"] == 1


def test_direct_memory_content_update(db, user_id):
    db.insert_memory(Memory(id="m2", user_id=user_id, content="Original"))
    db.conn.execute(
        "UPDATE memories SET content = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        ("Updated", "2026-01-01T00:00:00+00:00", user_id, "m2"),
    )
    db.conn.commit()
    result = _memory_row(db, user_id, "m2")
    assert result["content"] == "Updated"


def test_memory_supersession_fields_mark_old_row_inactive(db, user_id):
    db.insert_memory(Memory(id="m-old", user_id=user_id, content="Old"))
    with db.transaction():
        db.insert_memory(Memory(id="m-new", user_id=user_id, content="New"))
        db.conn.execute(
            "UPDATE memories SET superseded_by = ?, active = 0 WHERE user_id = ? AND id = ?",
            ("m-new", user_id, "m-old"),
        )
    assert _memory_row(db, user_id, "m-old")["active"] == 0
    assert _memory_row(db, user_id, "m-old")["superseded_by"] == "m-new"
    assert _memory_row(db, user_id, "m-new")["active"] == 1


def test_active_flag_can_retire_memory_row(db, user_id):
    db.insert_memory(Memory(id="m-deact", user_id=user_id, content="To deactivate"))
    db.conn.execute(
        "UPDATE memories SET active = 0 WHERE user_id = ? AND id = ?",
        (user_id, "m-deact"),
    )
    db.conn.commit()
    assert _memory_row(db, user_id, "m-deact")["active"] == 0


def test_memory_isolation(db):
    db.insert_memory(Memory(id="iso1", user_id="alice", content="Alice"))
    db.insert_memory(Memory(id="iso2", user_id="bob", content="Bob"))
    assert _memory_row(db, "alice", "iso2") is None
    assert db.count_memories("alice") == 1


def test_fts_search_reads_active_memory_rows(db, user_id):
    db.insert_memory(Memory(id="s1", user_id=user_id, content="Syke is an agentic memory system"))
    db.insert_memory(Memory(id="s2", user_id=user_id, content="Python programming"))
    db.insert_memory(Memory(id="s3", user_id=user_id, content="Memory and identity are the same"))
    ids = set(_search_memory_ids(db, user_id, "memory"))
    assert "s1" in ids and "s3" in ids


def test_fts_search_excludes_inactive_memory_rows(db, user_id):
    db.insert_memory(Memory(id="act", user_id=user_id, content="Active memory about Syke"))
    db.insert_memory(Memory(id="inact", user_id=user_id, content="Inactive memory about Syke"))
    db.conn.execute(
        "UPDATE memories SET active = 0 WHERE user_id = ? AND id = ?",
        (user_id, "inact"),
    )
    db.conn.commit()
    ids = set(_search_memory_ids(db, user_id, "Syke"))
    assert "act" in ids and "inact" not in ids


def test_links_bidirectional(db, user_id):
    db.insert_memory(Memory(id="ba", user_id=user_id, content="A"))
    db.insert_memory(Memory(id="bb", user_id=user_id, content="B"))
    db.conn.execute(
        """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("bilink", user_id, "ba", "bb", "Connected", "2026-01-01T00:00:00+00:00"),
    )
    db.conn.commit()
    assert _linked_memory_ids(db, user_id, "ba") == ["bb"]
    assert _linked_memory_ids(db, user_id, "bb") == ["ba"]


def test_update_memex(db, user_id):
    from syke.memory.memex import update_memex

    id1 = update_memex(db, user_id, "Version 1")
    assert db.get_memex(user_id)["content"] == "Version 1"
    id2 = update_memex(db, user_id, "Version 2")
    assert id2 != id1
    assert db.get_memex(user_id)["content"] == "Version 2"
    assert _memory_row(db, user_id, id1)["active"] == 0


def test_update_memex_collapses_duplicate_active_memex_rows(db, user_id):
    from syke.memory.memex import update_memex

    db.insert_memory(
        Memory(
            id="memex-older",
            user_id=user_id,
            content="older",
            source_event_ids=["__memex__"],
        )
    )
    db.insert_memory(
        Memory(
            id="memex-newer",
            user_id=user_id,
            content="newer",
            source_event_ids=["__memex__"],
        )
    )

    new_id = update_memex(db, user_id, "canonical")

    rows = db.conn.execute(
        """SELECT id, active, superseded_by
           FROM memories
           WHERE user_id = ? AND source_event_ids = ?
           ORDER BY id""",
        (user_id, '["__memex__"]'),
    ).fetchall()
    active_ids = [row["id"] for row in rows if row["active"]]
    assert active_ids == [new_id]
    assert _memory_row(db, user_id, "memex-older")["superseded_by"] == new_id
    assert _memory_row(db, user_id, "memex-newer")["superseded_by"] == new_id


def test_update_memex_collapses_duplicate_active_without_receipt(db, user_id):
    from datetime import UTC, datetime

    from syke.memory.memex import update_memex

    db.insert_memory(
        Memory(
            id="memex-older",
            user_id=user_id,
            content="canonical",
            source_event_ids=["__memex__"],
            created_at=datetime(2026, 5, 9, 12, 0, tzinfo=UTC),
        )
    )
    db.insert_memory(
        Memory(
            id="memex-newer",
            user_id=user_id,
            content="canonical",
            source_event_ids=["__memex__"],
            created_at=datetime(2026, 5, 9, 13, 0, tzinfo=UTC),
        )
    )

    kept_id = update_memex(db, user_id, "canonical")

    assert kept_id == "memex-newer"
    assert _memory_row(db, user_id, "memex-older")["active"] == 0


def test_update_memex_strips_projection_header(db, user_id):
    from syke.memory.memex import update_memex

    memex_id = update_memex(
        db,
        user_id,
        "# MEMEX [10 / 2,000 tokens · 1%]\n\ncanonical body",
    )

    assert _memory_row(db, user_id, memex_id)["content"] == "canonical body"


def test_get_memex_orders_mixed_timestamp_formats_by_instant(db, user_id):
    db.conn.execute(
        """INSERT INTO memories
           (id, user_id, content, source_event_ids, created_at, active)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (
            "memex-utc-earlier",
            user_id,
            "earlier utc row",
            '["__memex__"]',
            "2026-05-12T03:58:51+00:00",
        ),
    )
    db.conn.execute(
        """INSERT INTO memories
           (id, user_id, content, source_event_ids, created_at, active)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (
            "memex-local-later",
            user_id,
            "later offset row",
            '["__memex__"]',
            "2026-05-11T21:13:00-07:00",
        ),
    )
    db.conn.commit()

    assert db.get_memex(user_id)["id"] == "memex-local-later"


def test_get_memex_for_injection_no_data_fallback(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    result = get_memex_for_injection(db, user_id)
    assert "First run" in result
    assert "~15 minutes" not in result
    assert "syke status --json" in result


def test_insert_memory_standalone_commits(db, user_id):
    mem = Memory(id="m-standalone", user_id=user_id, content="standalone commit test")
    mid = db.insert_memory(mem)
    db2 = SykeDB(db.db_path)
    row = _memory_row(db2, user_id, mid)
    db2.close()
    assert row is not None
    assert row["content"] == "standalone commit test"


def test_insert_memory_in_transaction_defers(db, user_id):
    import sqlite3 as _sqlite3

    with db.transaction():
        mem = Memory(id="m-txn-defer", user_id=user_id, content="in-txn memory")
        mid = db.insert_memory(mem)
        conn2 = _sqlite3.connect(db.db_path, timeout=1)
        conn2.row_factory = _sqlite3.Row
        row = conn2.execute(
            "SELECT * FROM memories WHERE user_id = ? AND id = ?",
            (user_id, mid),
        ).fetchone()
        conn2.close()
        assert row is None
    db3 = SykeDB(db.db_path)
    row = _memory_row(db3, user_id, mid)
    db3.close()
    assert row is not None


def test_insert_cycle_record(db, user_id):
    cid = db.insert_cycle_record(user_id, cursor_start="evt-1", skill_hash="abc123")
    records = db.get_cycle_records(user_id)
    assert len(records) == 1
    assert records[0]["id"] == cid
    assert records[0]["status"] == "running"
    assert records[0]["cursor_start"] == "evt-1"
    assert records[0]["skill_hash"] == "abc123"


def test_insert_cycle_record_respects_started_at_override(db, user_id):
    cid = db.insert_cycle_record(
        user_id,
        cursor_start="evt-1",
        started_at_override="2026-03-07T23:59:00-08:00",
    )
    records = db.get_cycle_records(user_id)
    assert records[0]["id"] == cid
    assert records[0]["started_at"] == "2026-03-07T23:59:00-08:00"


def test_complete_cycle_record(db, user_id):
    cid = db.insert_cycle_record(user_id)
    db.complete_cycle_record(
        cid,
        status="completed",
        cursor_end="evt-99",
        memories_created=3,
        memex_updated=1,
    )
    records = db.get_cycle_records(user_id)
    assert records[0]["status"] == "completed"
    assert records[0]["cursor_end"] == "evt-99"
    assert records[0]["memories_created"] == 3
    assert records[0]["memex_updated"] == 1
    assert records[0]["completed_at"] is not None


def test_complete_cycle_record_preserves_existing_counters_when_omitted(db, user_id):
    cid = db.insert_cycle_record(user_id)
    db._conn.execute(
        """UPDATE cycle_records
           SET memories_created = 1, memories_updated = 2, links_created = 3, memex_updated = 1
           WHERE id = ?""",
        (cid,),
    )
    db._conn.commit()

    db.complete_cycle_record(cid, status="completed")

    records = db.get_cycle_records(user_id)
    assert records[0]["memories_created"] == 1
    assert records[0]["memories_updated"] == 2
    assert records[0]["links_created"] == 3
    assert records[0]["memex_updated"] == 1


def test_mark_stale_running_cycles_marks_only_old_running_rows(db, user_id):
    old_running = db.insert_cycle_record(
        user_id,
        started_at_override="2026-05-29T00:00:00+00:00",
    )
    recent_running = db.insert_cycle_record(
        user_id,
        started_at_override="2026-05-29T09:30:00+00:00",
    )
    completed = db.insert_cycle_record(
        user_id,
        started_at_override="2026-05-29T01:00:00+00:00",
    )
    db.complete_cycle_record(completed, status="completed")

    count = db.mark_stale_running_cycles(
        user_id,
        started_before="2026-05-29T06:00:00+00:00",
        completed_at_override="2026-05-29T10:00:00+00:00",
    )

    assert count == 1
    rows = {
        row["id"]: row
        for row in db.conn.execute(
            "SELECT id, status, completed_at, duration_ms FROM cycle_records"
        ).fetchall()
    }
    assert rows[old_running]["status"] == "incomplete"
    assert rows[old_running]["completed_at"] == "2026-05-29T10:00:00+00:00"
    assert rows[old_running]["duration_ms"] > 0
    assert rows[recent_running]["status"] == "running"
    assert rows[completed]["status"] == "completed"


def test_initialize_removes_legacy_tables_and_normalizes_cycle_residue(db, user_id):
    old_running = db.insert_cycle_record(
        user_id,
        started_at_override="2000-01-01T00:00:00+00:00",
    )
    recent_running = db.insert_cycle_record(
        user_id,
        started_at_override=datetime_now_utc_for_test(),
    )
    odd_status = db.insert_cycle_record(
        user_id,
        started_at_override="2000-01-01T01:00:00+00:00",
    )
    db.conn.execute("UPDATE cycle_records SET status = 'superseded' WHERE id = ?", (odd_status,))
    db.conn.execute(
        "CREATE TABLE memory_ops (id TEXT, user_id TEXT, operation TEXT, created_at TEXT)"
    )
    db.conn.execute("CREATE TABLE cycle_annotations (id TEXT, user_id TEXT, created_at TEXT)")
    db.conn.commit()

    db.initialize()

    tables = {
        row["name"]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "memory_ops" not in tables
    assert "cycle_annotations" not in tables
    rows = {
        row["id"]: row
        for row in db.conn.execute("SELECT id, status, completed_at FROM cycle_records").fetchall()
    }
    assert rows[old_running]["status"] == "incomplete"
    assert rows[old_running]["completed_at"] is not None
    assert rows[recent_running]["status"] == "running"
    assert rows[odd_status]["status"] == "incomplete"
    assert rows[odd_status]["completed_at"] is not None


def datetime_now_utc_for_test() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def test_pi_skill_file_present() -> None:
    from syke.runtime.psyche_md import SYNTHESIS_PATH

    assert SYNTHESIS_PATH.exists()
    assert SYNTHESIS_PATH.read_text(encoding="utf-8").strip()


def test_fts5_trigger_on_insert(db, user_id):
    mem = Memory(id="fts-ins-1", user_id=user_id, content="quantum computing research")
    db.insert_memory(mem)
    ids = _search_memory_ids(db, user_id, "quantum computing")
    assert "fts-ins-1" in ids


def test_fts5_trigger_on_direct_content_update(db, user_id):
    mem = Memory(id="fts-upd-1", user_id=user_id, content="old content about dogs")
    db.insert_memory(mem)
    assert _search_memory_ids(db, user_id, "dogs")
    db.conn.execute(
        "UPDATE memories SET content = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        ("new content about cats", "2026-01-01T00:00:00+00:00", user_id, "fts-upd-1"),
    )
    db.conn.commit()
    assert not _search_memory_ids(db, user_id, "dogs")
    ids = _search_memory_ids(db, user_id, "cats")
    assert "fts-upd-1" in ids


def test_fts5_trigger_on_active_flag_retirement(db, user_id):
    mem = Memory(id="fts-deact-1", user_id=user_id, content="ephemeral knowledge")
    db.insert_memory(mem)
    assert _search_memory_ids(db, user_id, "ephemeral")
    db.conn.execute(
        "UPDATE memories SET active = 0 WHERE user_id = ? AND id = ?",
        (user_id, "fts-deact-1"),
    )
    db.conn.commit()
    assert not _search_memory_ids(db, user_id, "ephemeral")


def test_fts5_trigger_on_supersession_update(db, user_id):
    old = Memory(id="fts-sup-old", user_id=user_id, content="original fact about mars")
    db.insert_memory(old)
    with db.transaction():
        db.insert_memory(
            Memory(id="fts-sup-new", user_id=user_id, content="updated fact about jupiter")
        )
        db.conn.execute(
            "UPDATE memories SET superseded_by = ?, active = 0 WHERE user_id = ? AND id = ?",
            ("fts-sup-new", user_id, "fts-sup-old"),
        )
    assert not _search_memory_ids(db, user_id, "mars")
    ids = _search_memory_ids(db, user_id, "jupiter")
    assert "fts-sup-new" in ids


def test_link_insert_in_transaction_defers(db, user_id):
    """Direct link-row inserts must defer commit inside db.transaction()."""
    import sqlite3 as _sqlite3

    db.insert_memory(Memory(id="link-a", user_id=user_id, content="A"))
    db.insert_memory(Memory(id="link-b", user_id=user_id, content="B"))

    with db.transaction():
        db.conn.execute(
            """INSERT INTO links (id, user_id, source_id, target_id, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("txn-link", user_id, "link-a", "link-b", "test", "2026-01-01T00:00:00+00:00"),
        )
        conn2 = _sqlite3.connect(db.db_path, timeout=1)
        row = conn2.execute("SELECT * FROM links WHERE id = ?", ("txn-link",)).fetchone()
        conn2.close()
        assert row is None

    conn3 = _sqlite3.connect(db.db_path, timeout=1)
    row = conn3.execute("SELECT * FROM links WHERE id = ?", ("txn-link",)).fetchone()
    conn3.close()
    assert row is not None


def test_transaction_reentrant(db, user_id):
    """Nested transaction() calls pass through — outermost controls commit."""
    import sqlite3 as _sqlite3

    from syke.memory.memex import update_memex

    with db.transaction():
        db.insert_memory(Memory(id="outer", user_id=user_id, content="outer"))
        memex_id = update_memex(db, user_id, "inner memex")

        conn2 = _sqlite3.connect(db.db_path, timeout=1)
        row = conn2.execute("SELECT * FROM memories WHERE id = ?", ("outer",)).fetchone()
        memex_row = conn2.execute("SELECT * FROM memories WHERE id = ?", (memex_id,)).fetchone()
        conn2.close()
        assert row is None
        assert memex_row is None

    assert _memory_row(db, user_id, "outer") is not None
    assert _memory_row(db, user_id, memex_id) is not None
