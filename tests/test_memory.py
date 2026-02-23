"""Tests for the memory layer — schema, CRUD, FTS5, links, memex, tools."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from syke.db import SykeDB
from syke.models import Memory, Link, Event, UserProfile


# ---------------------------------------------------------------------------
# Schema & migrations
# ---------------------------------------------------------------------------


def test_memory_tables_exist(db, user_id):
    tables = {
        row[0]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memories" in tables
    assert "links" in tables
    assert "memory_ops" in tables


def test_fts_tables_exist(db, user_id):
    tables = {
        row[0]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "memories_fts" in tables
    assert "events_fts" in tables


# ---------------------------------------------------------------------------
# Memory CRUD
# ---------------------------------------------------------------------------


def test_insert_and_get_memory(db, user_id):
    mem = Memory(id="m1", user_id=user_id, content="Utkarsh loves building AI agents")
    db.insert_memory(mem)

    result = db.get_memory(user_id, "m1")
    assert result is not None
    assert result["content"] == "Utkarsh loves building AI agents"
    assert result["active"] == 1


def test_update_memory(db, user_id):
    mem = Memory(id="m2", user_id=user_id, content="Original content")
    db.insert_memory(mem)

    db.update_memory(user_id, "m2", new_content="Updated content")
    result = db.get_memory(user_id, "m2")
    assert result["content"] == "Updated content"
    assert result["updated_at"] is not None


def test_supersede_memory(db, user_id):
    old = Memory(id="m-old", user_id=user_id, content="Old version")
    db.insert_memory(old)

    new = Memory(id="m-new", user_id=user_id, content="New version")
    new_id = db.supersede_memory(user_id, "m-old", new)
    assert new_id == "m-new"

    old_result = db.get_memory(user_id, "m-old")
    assert old_result["active"] == 0
    assert old_result["superseded_by"] == "m-new"

    new_result = db.get_memory(user_id, "m-new")
    assert new_result["active"] == 1
    assert new_result["content"] == "New version"


def test_deactivate_memory(db, user_id):
    mem = Memory(id="m-deact", user_id=user_id, content="To be deactivated")
    db.insert_memory(mem)

    db.deactivate_memory(user_id, "m-deact")
    result = db.get_memory(user_id, "m-deact")
    assert result["active"] == 0


def test_count_memories(db, user_id):
    assert db.count_memories(user_id) == 0
    db.insert_memory(Memory(id="c1", user_id=user_id, content="First"))
    db.insert_memory(Memory(id="c2", user_id=user_id, content="Second"))
    assert db.count_memories(user_id) == 2


def test_get_recent_memories(db, user_id):
    for i in range(5):
        db.insert_memory(Memory(id=f"r{i}", user_id=user_id, content=f"Memory {i}"))

    recent = db.get_recent_memories(user_id, limit=3)
    assert len(recent) == 3


def test_memory_isolation(db):
    db.insert_memory(Memory(id="iso1", user_id="alice", content="Alice memory"))
    db.insert_memory(Memory(id="iso2", user_id="bob", content="Bob memory"))

    assert db.get_memory("alice", "iso2") is None
    assert db.count_memories("alice") == 1
    assert db.count_memories("bob") == 1


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------


def test_search_memories_fts(db, user_id):
    db.insert_memory(
        Memory(id="s1", user_id=user_id, content="Syke is an agentic memory system")
    )
    db.insert_memory(
        Memory(id="s2", user_id=user_id, content="Python programming language")
    )
    db.insert_memory(
        Memory(id="s3", user_id=user_id, content="Memory and identity are the same")
    )

    results = db.search_memories(user_id, "memory")
    assert len(results) >= 2
    ids = {r["id"] for r in results}
    assert "s1" in ids
    assert "s3" in ids


def test_search_memories_excludes_inactive(db, user_id):
    db.insert_memory(
        Memory(id="act", user_id=user_id, content="Active memory about Syke")
    )
    mem = Memory(id="inact", user_id=user_id, content="Inactive memory about Syke")
    db.insert_memory(mem)
    db.deactivate_memory(user_id, "inact")

    results = db.search_memories(user_id, "Syke")
    ids = {r["id"] for r in results}
    assert "act" in ids
    assert "inact" not in ids


def test_search_events_fts(db, user_id):
    db.insert_event(
        Event(
            user_id=user_id,
            source="github",
            timestamp=datetime(2025, 2, 1),
            event_type="commit",
            title="Refactor auth module",
            content="JWT tokens replaced",
        )
    )
    db.insert_event(
        Event(
            user_id=user_id,
            source="gmail",
            timestamp=datetime(2025, 2, 2),
            event_type="email",
            title="Meeting notes",
            content="Discussed roadmap",
        )
    )

    results = db.search_events_fts(user_id, "auth")
    assert len(results) >= 1
    assert results[0]["title"] == "Refactor auth module"


def test_supersede_syncs_fts(db, user_id):
    old = Memory(id="fts-old", user_id=user_id, content="Old searchable content")
    db.insert_memory(old)

    results_before = db.search_memories(user_id, "searchable")
    assert any(r["id"] == "fts-old" for r in results_before)

    new = Memory(id="fts-new", user_id=user_id, content="New content entirely")
    db.supersede_memory(user_id, "fts-old", new)

    results_after = db.search_memories(user_id, "searchable")
    assert not any(r["id"] == "fts-old" for r in results_after)

    results_new = db.search_memories(user_id, "entirely")
    assert any(r["id"] == "fts-new" for r in results_new)


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_insert_and_get_links(db, user_id):
    db.insert_memory(Memory(id="la", user_id=user_id, content="Memory A"))
    db.insert_memory(Memory(id="lb", user_id=user_id, content="Memory B"))

    link = Link(
        id="link1",
        user_id=user_id,
        source_id="la",
        target_id="lb",
        reason="Related topics",
    )
    db.insert_link(link)

    links = db.get_links_for(user_id, "la")
    assert len(links) == 1
    assert links[0]["reason"] == "Related topics"


def test_links_bidirectional(db, user_id):
    db.insert_memory(Memory(id="ba", user_id=user_id, content="Memory A"))
    db.insert_memory(Memory(id="bb", user_id=user_id, content="Memory B"))

    link = Link(
        id="bilink", user_id=user_id, source_id="ba", target_id="bb", reason="Connected"
    )
    db.insert_link(link)

    linked_from_a = db.get_linked_memories(user_id, "ba")
    linked_from_b = db.get_linked_memories(user_id, "bb")

    assert len(linked_from_a) == 1
    assert linked_from_a[0]["id"] == "bb"
    assert len(linked_from_b) == 1
    assert linked_from_b[0]["id"] == "ba"


# ---------------------------------------------------------------------------
# Memex convention
# ---------------------------------------------------------------------------


def test_memex_convention(db, user_id):
    memex = Memory(
        id="memex1",
        user_id=user_id,
        content="# Memex — test_user\nWorld index content",
        source_event_ids=["__memex__"],
    )
    db.insert_memory(memex)

    result = db.get_memex(user_id)
    assert result is not None
    assert result["id"] == "memex1"
    assert "World index content" in result["content"]


def test_no_memex_returns_none(db, user_id):
    assert db.get_memex(user_id) is None


# ---------------------------------------------------------------------------
# Operation logging
# ---------------------------------------------------------------------------


def test_log_memory_op(db, user_id):
    db.log_memory_op(
        user_id,
        "add",
        input_summary="test input",
        output_summary="test output",
        memory_ids=["m1", "m2"],
        duration_ms=42,
    )

    ops = db.get_memory_ops(user_id, limit=10)
    assert len(ops) == 1
    assert ops[0]["operation"] == "add"
    assert ops[0]["duration_ms"] == 42
    assert "m1" in ops[0]["memory_ids"]


def test_get_last_synthesis_timestamp(db, user_id):
    assert db.get_last_synthesis_timestamp(user_id) is None

    db.log_memory_op(user_id, "synthesize", input_summary="test")

    ts = db.get_last_synthesis_timestamp(user_id)
    assert ts is not None


# ---------------------------------------------------------------------------
# Memex helper functions
# ---------------------------------------------------------------------------


def test_bootstrap_memex_from_profile(db, user_id):
    from syke.memory.memex import bootstrap_memex_from_profile

    profile = UserProfile(
        user_id=user_id,
        identity_anchor="A curious builder",
        sources=["github", "chatgpt"],
        events_count=100,
    )
    db.save_profile(profile)

    memex_id = bootstrap_memex_from_profile(db, user_id)
    assert memex_id is not None

    memex = db.get_memex(user_id)
    assert memex is not None
    assert "curious builder" in memex["content"]


def test_bootstrap_memex_idempotent(db, user_id):
    from syke.memory.memex import bootstrap_memex_from_profile

    profile = UserProfile(
        user_id=user_id,
        identity_anchor="Builder",
        sources=["github"],
        events_count=50,
    )
    db.save_profile(profile)

    id1 = bootstrap_memex_from_profile(db, user_id)
    id2 = bootstrap_memex_from_profile(db, user_id)
    assert id1 == id2


def test_get_memex_for_injection(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    result = get_memex_for_injection(db, user_id)
    assert "[No data yet.]" in result

    db.insert_memory(
        Memory(
            id="memex-inj",
            user_id=user_id,
            content="# Memex\nInjected content",
            source_event_ids=["__memex__"],
        )
    )

    result = get_memex_for_injection(db, user_id)
    assert "Injected content" in result


def test_get_memex_for_injection_auto_bootstrap(tmp_path, user_id):
    """When profile exists but no memex, get_memex_for_injection auto-bootstraps."""
    from syke.db import SykeDB
    from syke.models import UserProfile
    from syke.memory.memex import get_memex_for_injection

    db = SykeDB(str(tmp_path / "test.db"))
    db.save_profile(UserProfile(
        user_id=user_id,
        identity_anchor="A curious builder exploring AI tools",
        sources=["github"],
        events_count=10,
    ))

    # No memex exists yet
    assert db.get_memex(user_id) is None

    result = get_memex_for_injection(db, user_id)

    # Should have bootstrapped and returned memex content
    assert "curious builder" in result.lower()
    assert "[No data yet.]" not in result
    # Memex should now exist in DB
    assert db.get_memex(user_id) is not None

def test_update_memex(db, user_id):
    from syke.memory.memex import update_memex

    id1 = update_memex(db, user_id, "Version 1")
    assert db.get_memex(user_id)["content"] == "Version 1"

    id2 = update_memex(db, user_id, "Version 2")
    assert id2 != id1
    assert db.get_memex(user_id)["content"] == "Version 2"

    old = db.get_memory(user_id, id1)
    assert old["active"] == 0


# ---------------------------------------------------------------------------
# Consolidator helpers
# ---------------------------------------------------------------------------


def test_should_synthesize_threshold(db, user_id):
    from syke.memory.synthesis import _should_synthesize

    assert not _should_synthesize(db, user_id)

    for i in range(5):
        db.insert_event(
            Event(
                user_id=user_id,
                source="test",
                timestamp=datetime(2025, 3, 1 + i),
                event_type="test",
                title=f"Event {i}",
                content=f"Content {i}",
            )
        )

    assert _should_synthesize(db, user_id)


def test_extract_memex_content():
    from syke.memory.synthesis import _extract_memex_content

    text = "Some preamble\n<memex>\n# Memex\nContent here\n</memex>\nEpilogue"
    result = _extract_memex_content(text)
    assert result == "# Memex\nContent here"

    assert _extract_memex_content("No tags here") is None


def test_get_new_events_summary(db, user_id):
    from syke.memory.synthesis import _get_new_events_summary

    assert "[No new events]" in _get_new_events_summary(db, user_id)

    db.insert_event(
        Event(
            user_id=user_id,
            source="github",
            timestamp=datetime(2025, 3, 1),
            event_type="commit",
            title="Fix bug",
            content="Fixed the auth bug",
        )
    )

    summary = _get_new_events_summary(db, user_id)
    assert "Fix bug" in summary
    assert "github" in summary
