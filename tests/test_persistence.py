"""Tests for the persistence layer."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from syke.db import SykeDB
from syke.models import Event, Link, Memory


def _evt(
    user_id: str,
    title: str = "Test",
    content: str = "Content",
    source: str = "test",
    **kw: Any,
) -> Event:
    return Event(
        user_id=user_id,
        source=source,
        timestamp=kw.pop("timestamp", datetime(2025, 1, 15, 12, 0)),
        event_type=kw.pop("event_type", "test"),
        title=title,
        content=content,
        **kw,
    )


def _insert_events(db: SykeDB, user_id: str, count: int, *, start: int = 0) -> list[str]:
    base = datetime(2025, 1, 15, 12, 0)
    ids: list[str] = []
    for idx in range(start, start + count):
        event = _evt(
            user_id,
            title=f"Event {idx}",
            content=f"Content {idx}",
            timestamp=base + timedelta(minutes=idx),
        )
        assert db.insert_event(event)
        ids.append(cast(str, event.id))
    return ids


def test_insert_and_query_event(db, user_id):
    event = _evt(user_id, title="Test Event", content="This is test content.")
    assert db.insert_event(event) is True
    events = db.get_events(user_id)
    assert len(events) == 1
    assert events[0]["title"] == "Test Event"


def test_event_metadata_alias_maps_to_canonical_extras(db, user_id):
    event = Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 15, 12, 0),
        event_type="test",
        content="payload",
        metadata={"tag": "work"},
    )

    assert event.extras == {"tag": "work"}
    assert event.metadata == {"tag": "work"}
    assert db.insert_event(event) is True

    row = db.get_events(user_id)[0]
    assert row["metadata"] == '{"tag": "work"}'
    assert row["extras"] == '{"tag": "work"}'


def test_event_rejects_conflicting_metadata_and_extras():
    with pytest.raises(ValueError, match="ambiguous"):
        Event(
            user_id="u1",
            source="test",
            timestamp=datetime(2025, 1, 15, 12, 0),
            event_type="test",
            content="payload",
            metadata={"tag": "old"},
            extras={"tag": "new"},
        )


def test_dedup(db, user_id):
    event = _evt(user_id, title="Duplicate", content="Same event.")
    assert db.insert_event(event) is True
    assert db.insert_event(event) is False
    assert db.count_events(user_id) == 1


def test_count_and_sources(db, user_id):
    for i, src in enumerate(["gmail", "gmail", "github"]):
        db.insert_event(
            _evt(
                user_id,
                title=f"Event {i}",
                content=f"Content {i}",
                source=src,
                timestamp=datetime(2025, 1, 15 + i, 12, 0),
            )
        )
    assert db.count_events(user_id) == 3
    assert db.count_events(user_id, "gmail") == 2
    assert set(db.get_sources(user_id)) == {"gmail", "github"}


@pytest.mark.parametrize(
    "query_user,expected_found",
    [("test_user", True), ("other_user", False)],
)
def test_get_event_by_id(db, user_id, query_user, expected_found):
    db.insert_event(_evt(user_id, title="Findable"))
    events = db.get_events(user_id)
    event_id = events[0]["id"]
    result = db.get_event_by_id(query_user, event_id)
    assert (result is not None) is expected_found


def test_ingestion_run(db, user_id):
    run_id = db.start_ingestion_run(user_id, "test")
    db.complete_ingestion_run(run_id, 42)
    status = db.get_status(user_id)
    assert len(status["recent_runs"]) == 1
    assert status["recent_runs"][0]["events_count"] == 42


def test_migration_idempotent(tmp_path: Path):
    db = SykeDB(tmp_path / "idem.db")
    db.initialize()
    db.initialize()
    assert db.count_events("nobody") == 0
    db.close()


def test_insert_and_get_memory(db, user_id):
    db.insert_memory(Memory(id="m1", user_id=user_id, content="Utkarsh loves AI agents"))
    result = db.get_memory(user_id, "m1")
    assert result is not None
    assert result["content"] == "Utkarsh loves AI agents"
    assert result["active"] == 1


def test_update_memory(db, user_id):
    db.insert_memory(Memory(id="m2", user_id=user_id, content="Original"))
    db.update_memory(user_id, "m2", new_content="Updated")
    result = db.get_memory(user_id, "m2")
    assert result["content"] == "Updated"


def test_supersede_memory(db, user_id):
    db.insert_memory(Memory(id="m-old", user_id=user_id, content="Old"))
    new = Memory(id="m-new", user_id=user_id, content="New")
    db.supersede_memory(user_id, "m-old", new)
    assert db.get_memory(user_id, "m-old")["active"] == 0
    assert db.get_memory(user_id, "m-old")["superseded_by"] == "m-new"
    assert db.get_memory(user_id, "m-new")["active"] == 1


def test_deactivate_memory(db, user_id):
    db.insert_memory(Memory(id="m-deact", user_id=user_id, content="To deactivate"))
    db.deactivate_memory(user_id, "m-deact")
    assert db.get_memory(user_id, "m-deact")["active"] == 0


def test_memory_isolation(db):
    db.insert_memory(Memory(id="iso1", user_id="alice", content="Alice"))
    db.insert_memory(Memory(id="iso2", user_id="bob", content="Bob"))
    assert db.get_memory("alice", "iso2") is None
    assert db.count_memories("alice") == 1


def test_search_memories_fts(db, user_id):
    db.insert_memory(Memory(id="s1", user_id=user_id, content="Syke is an agentic memory system"))
    db.insert_memory(Memory(id="s2", user_id=user_id, content="Python programming"))
    db.insert_memory(Memory(id="s3", user_id=user_id, content="Memory and identity are the same"))
    results = db.search_memories(user_id, "memory")
    ids = {r["id"] for r in results}
    assert "s1" in ids and "s3" in ids


def test_search_memories_excludes_inactive(db, user_id):
    db.insert_memory(Memory(id="act", user_id=user_id, content="Active memory about Syke"))
    db.insert_memory(Memory(id="inact", user_id=user_id, content="Inactive memory about Syke"))
    db.deactivate_memory(user_id, "inact")
    ids = {r["id"] for r in db.search_memories(user_id, "Syke")}
    assert "act" in ids and "inact" not in ids


def test_search_events_fts(db, user_id):
    db.insert_event(
        _evt(
            user_id,
            title="Refactor auth",
            content="JWT tokens replaced",
            source="github",
            event_type="commit",
            timestamp=datetime(2025, 2, 1),
        )
    )
    db.insert_event(
        _evt(
            user_id,
            title="Meeting notes",
            content="Discussed roadmap",
            source="gmail",
            event_type="email",
            timestamp=datetime(2025, 2, 2),
        )
    )
    results = db.search_events_fts(user_id, "auth")
    assert len(results) >= 1
    assert results[0]["title"] == "Refactor auth"


def test_links_bidirectional(db, user_id):
    db.insert_memory(Memory(id="ba", user_id=user_id, content="A"))
    db.insert_memory(Memory(id="bb", user_id=user_id, content="B"))
    db.insert_link(
        Link(
            id="bilink",
            user_id=user_id,
            source_id="ba",
            target_id="bb",
            reason="Connected",
        )
    )
    assert len(db.get_linked_memories(user_id, "ba")) == 1
    assert db.get_linked_memories(user_id, "ba")[0]["id"] == "bb"
    assert db.get_linked_memories(user_id, "bb")[0]["id"] == "ba"


def test_update_memex(db, user_id):
    from syke.memory.memex import update_memex

    id1 = update_memex(db, user_id, "Version 1")
    assert db.get_memex(user_id)["content"] == "Version 1"
    id2 = update_memex(db, user_id, "Version 2")
    assert id2 != id1
    assert db.get_memex(user_id)["content"] == "Version 2"
    assert db.get_memory(user_id, id1)["active"] == 0


def test_log_memory_op(db, user_id):
    db.log_memory_op(
        user_id,
        "add",
        input_summary="test",
        output_summary="out",
        memory_ids=["m1"],
        duration_ms=42,
    )
    ops = db.get_memory_ops(user_id, limit=10)
    assert len(ops) == 1 and ops[0]["operation"] == "add" and ops[0]["duration_ms"] == 42


def test_get_memex_for_injection_no_data_fallback(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    assert get_memex_for_injection(db, user_id) == "[No data yet.]"


def test_insert_memory_standalone_commits(db, user_id):
    mem = Memory(id="m-standalone", user_id=user_id, content="standalone commit test")
    mid = db.insert_memory(mem)
    db2 = SykeDB(db.db_path)
    row = db2.get_memory(user_id, mid)
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
    row = db3.get_memory(user_id, mid)
    db3.close()
    assert row is not None


def test_supersede_memory_atomic(db, user_id):
    old = Memory(id="m-atom-old", user_id=user_id, content="original")
    old_id = db.insert_memory(old)
    new = Memory(id="m-atom-new", user_id=user_id, content="replacement")
    new_id = db.supersede_memory(user_id, old_id, new)
    old_row = db.get_memory(user_id, old_id)
    new_row = db.get_memory(user_id, new_id)
    assert old_row is not None
    assert old_row["active"] == 0
    assert old_row["superseded_by"] == new_id
    assert new_row is not None
    assert new_row["content"] == "replacement"


def test_insert_cycle_record(db, user_id):
    cid = db.insert_cycle_record(user_id, cursor_start="evt-1", skill_hash="abc123")
    records = db.get_cycle_records(user_id)
    assert len(records) == 1
    assert records[0]["id"] == cid
    assert records[0]["status"] == "running"
    assert records[0]["cursor_start"] == "evt-1"
    assert records[0]["skill_hash"] == "abc123"


def test_complete_cycle_record(db, user_id):
    cid = db.insert_cycle_record(user_id)
    db.complete_cycle_record(
        cid,
        status="completed",
        cursor_end="evt-99",
        events_processed=10,
        memories_created=3,
        memex_updated=1,
    )
    records = db.get_cycle_records(user_id)
    assert records[0]["status"] == "completed"
    assert records[0]["cursor_end"] == "evt-99"
    assert records[0]["events_processed"] == 10
    assert records[0]["memories_created"] == 3
    assert records[0]["memex_updated"] == 1
    assert records[0]["completed_at"] is not None


def test_insert_cycle_annotation(db, user_id):
    cid = db.insert_cycle_record(user_id)
    aid = db.insert_cycle_annotation(cid, "synthesis", "reflection", "cycle went well")
    rows = db._conn.execute("SELECT * FROM cycle_annotations WHERE cycle_id = ?", (cid,)).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["id"] == aid
    assert row["annotator"] == "synthesis"
    assert row["annotation_type"] == "reflection"
    assert row["content"] == "cycle went well"


def test_commit_cycle_advances_cursor(db, user_id):
    db.set_synthesis_cursor(user_id, "old-cursor")
    assert db.get_synthesis_cursor(user_id) == "old-cursor"
    db.set_synthesis_cursor(user_id, "new-cursor")
    assert db.get_synthesis_cursor(user_id) == "new-cursor"


def test_pi_skill_file_present() -> None:
    from syke.llm.backends.pi_synthesis import SKILL_PATH

    assert SKILL_PATH.exists()
    assert SKILL_PATH.read_text(encoding="utf-8").strip()


def test_fts5_trigger_on_insert(db, user_id):
    mem = Memory(id="fts-ins-1", user_id=user_id, content="quantum computing research")
    db.insert_memory(mem)
    results = db.search_memories(user_id, "quantum computing")
    ids = [r["id"] for r in results]
    assert "fts-ins-1" in ids


def test_fts5_trigger_on_update(db, user_id):
    mem = Memory(id="fts-upd-1", user_id=user_id, content="old content about dogs")
    db.insert_memory(mem)
    assert db.search_memories(user_id, "dogs")
    db.update_memory(user_id, "fts-upd-1", "new content about cats")
    assert not db.search_memories(user_id, "dogs")
    results = db.search_memories(user_id, "cats")
    ids = [r["id"] for r in results]
    assert "fts-upd-1" in ids


def test_fts5_trigger_on_deactivate(db, user_id):
    mem = Memory(id="fts-deact-1", user_id=user_id, content="ephemeral knowledge")
    db.insert_memory(mem)
    assert db.search_memories(user_id, "ephemeral")
    db.deactivate_memory(user_id, "fts-deact-1")
    assert not db.search_memories(user_id, "ephemeral")


def test_fts5_trigger_on_supersede(db, user_id):
    old = Memory(id="fts-sup-old", user_id=user_id, content="original fact about mars")
    db.insert_memory(old)
    new = Memory(id="fts-sup-new", user_id=user_id, content="updated fact about jupiter")
    db.supersede_memory(user_id, "fts-sup-old", new)
    assert not db.search_memories(user_id, "mars")
    results = db.search_memories(user_id, "jupiter")
    ids = [r["id"] for r in results]
    assert "fts-sup-new" in ids
