"""Tests for the memory layer — schema, CRUD, FTS5, links, memex, tools."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

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


# ---------------------------------------------------------------------------
# Additional memex coverage
# ---------------------------------------------------------------------------


def _insert_profile_row(
    db: SykeDB, profile: UserProfile, profile_id: str = "profile-1"
) -> None:
    db.conn.execute(
        """INSERT INTO profiles
           (id, user_id, created_at, profile_json, events_count, sources, model, cost_usd, thinking_tokens)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile_id,
            profile.user_id,
            profile.created_at.isoformat(),
            profile.model_dump_json(),
            profile.events_count,
            json.dumps(profile.sources),
            profile.model,
            profile.cost_usd,
            profile.thinking_tokens,
        ),
    )
    db.conn.commit()


def test_bootstrap_memex_from_profile_existing_memex_returns_existing_id(db, user_id):
    from syke.memory.memex import bootstrap_memex_from_profile

    existing_id = "memex-existing"
    db.insert_memory(
        Memory(
            id=existing_id,
            user_id=user_id,
            content="# Memex\nAlready present",
            source_event_ids=["__memex__"],
        )
    )

    returned_id = bootstrap_memex_from_profile(db, user_id)

    assert returned_id == existing_id
    memex_rows = db.conn.execute(
        "SELECT id FROM memories WHERE user_id = ? AND source_event_ids = ?",
        (user_id, json.dumps(["__memex__"])),
    ).fetchall()
    assert len(memex_rows) == 1


def test_bootstrap_memex_from_profile_no_profile_returns_none(db, user_id):
    from syke.memory.memex import bootstrap_memex_from_profile

    returned_id = bootstrap_memex_from_profile(db, user_id)

    assert returned_id is None
    assert db.get_memex(user_id) is None


def test_bootstrap_memex_from_profile_creates_memex_from_profile(db, user_id):
    from syke.memory.memex import bootstrap_memex_from_profile
    from syke.models import UserProfile, ActiveThread, VoicePattern as VoicePatterns

    profile = UserProfile(
        user_id=user_id,
        identity_anchor="Engineer building memory systems.",
        active_threads=[
            ActiveThread(
                name="Syke",
                description="Adding memex coverage",
                intensity="high",
                platforms=["github", "claude-code"],
                recent_signals=["Wrote failing tests", "Added coverage"],
            )
        ],
        world_state="Release prep week.",
        recent_detail="Working on memory reliability.",
        background_context="Long-term focus on agent infra.",
        voice_patterns=VoicePatterns(tone="direct", communication_style="concise"),
        sources=["github", "claude-code"],
        events_count=42,
    )
    _insert_profile_row(db, profile)

    new_id = bootstrap_memex_from_profile(db, user_id)

    assert new_id is not None
    memex = db.get_memex(user_id)
    assert memex is not None
    assert memex["id"] == new_id
    content = memex["content"]
    assert content.startswith(f"# Memex — {user_id}")
    assert "## Identity" in content
    assert "## What's Active" in content
    assert "## Context" in content
    assert "## Recent Context" in content
    assert "## Background" in content
    assert "## Voice" in content
    assert "Sources: github, claude-code. Events: 42." in content


def test_profile_to_memex_content_minimal_profile():
    from syke.memory.memex import _profile_to_memex_content
    from syke.models import UserProfile

    profile = UserProfile(user_id="test_user", identity_anchor="Builder and operator.")

    content = _profile_to_memex_content(profile)

    assert content.startswith("# Memex — test_user")
    assert "## Identity" in content
    assert "Builder and operator." in content
    assert "## What's Active" not in content
    assert "## Context" not in content
    assert "## Recent Context" not in content
    assert "## Background" not in content
    assert "## Voice" not in content
    assert "Sources: . Events: 0." in content


def test_profile_to_memex_content_full_profile_sections_and_sources_footer():
    from syke.memory.memex import _profile_to_memex_content
    from syke.models import UserProfile, ActiveThread, VoicePattern as VoicePatterns

    profile = UserProfile(
        user_id="test_user",
        identity_anchor="AI engineer and founder.",
        active_threads=[
            ActiveThread(
                name="Product launch",
                description="Finalizing release tasks",
                intensity="medium",
                platforms=["github", "gmail"],
                recent_signals=["Tagged rc1", "Sent launch draft", "Updated changelog"],
            )
        ],
        world_state="Balancing launch and support.",
        recent_detail="Shipping bugfixes daily.",
        background_context="Building AI-native developer tools.",
        voice_patterns=VoicePatterns(tone="technical", communication_style="direct"),
        sources=["github", "gmail", "claude-code"],
        events_count=123,
    )

    content = _profile_to_memex_content(profile)

    assert "# Memex — test_user" in content
    assert "## Identity" in content
    assert "## What's Active" in content
    assert (
        "- **Product launch** [medium] (github, gmail): Finalizing release tasks"
        in content
    )
    assert "  - Tagged rc1" in content
    assert "## Context" in content
    assert "## Recent Context" in content
    assert "## Background" in content
    assert "## Voice" in content
    assert "Tone: technical" in content
    assert "Style: direct" in content
    assert "Sources: github, gmail, claude-code. Events: 123." in content


def test_get_memex_for_injection_no_memex_no_profile_has_memories(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    db.insert_memory(Memory(id="m-no-memex-1", user_id=user_id, content="Some memory"))
    db.insert_memory(
        Memory(id="m-no-memex-2", user_id=user_id, content="Another memory")
    )

    result = get_memex_for_injection(db, user_id)

    assert result == (
        "[No memex yet. 2 memories and 0 events available. "
        "Use search_memories and search_evidence to explore.]"
    )


def test_get_memex_for_injection_no_memex_no_profile_has_events_only(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    db.insert_event(
        Event(
            user_id=user_id,
            source="github",
            timestamp=datetime(2025, 2, 1),
            event_type="commit",
            title="Add feature",
            content="Implemented feature",
        )
    )
    db.insert_event(
        Event(
            user_id=user_id,
            source="gmail",
            timestamp=datetime(2025, 2, 2),
            event_type="email",
            title="Review notes",
            content="Please review",
        )
    )

    result = get_memex_for_injection(db, user_id)

    assert result == (
        "[No memories yet. 2 raw events available. "
        "Use search_evidence to explore raw events.]"
    )


def test_get_memex_for_injection_auto_bootstraps_from_profile(db, user_id):
    from syke.memory.memex import get_memex_for_injection
    from syke.models import UserProfile, ActiveThread, VoicePattern as VoicePatterns

    profile = UserProfile(
        user_id=user_id,
        identity_anchor="Context-first engineer.",
        active_threads=[
            ActiveThread(
                name="Memex tests",
                description="Expanding edge-case coverage",
                intensity="high",
                platforms=["github"],
                recent_signals=["Added bootstrap tests"],
            )
        ],
        world_state="Focused on test quality.",
        recent_detail="Covering fallback and bootstrap paths.",
        background_context="Prefers deterministic tests.",
        voice_patterns=VoicePatterns(tone="direct", communication_style="precise"),
        sources=["github"],
        events_count=7,
    )
    _insert_profile_row(db, profile)

    result = get_memex_for_injection(db, user_id)

    memex = db.get_memex(user_id)
    assert memex is not None
    assert result == memex["content"]
    assert result.startswith(f"# Memex — {user_id}")
    assert "## Identity" in result
    assert "Sources: github. Events: 7." in result


def test_synthesize_skips_below_threshold(db, user_id):
    from syke.memory.synthesis import synthesize

    result = synthesize(db, user_id)

    assert result == {"status": "skipped", "reason": "below_threshold"}


def test_synthesize_force_bypasses_threshold(db, user_id):
    from syke.memory.synthesis import synthesize

    expected = {
        "status": "ok",
        "cost_usd": 0.0,
        "num_turns": 0,
        "memex_updated": False,
    }

    with patch(
        "syke.memory.synthesis._run_synthesis", new=AsyncMock(return_value=expected)
    ) as mock_run:
        result = synthesize(db, user_id, force=True)

    assert result == expected
    mock_run.assert_awaited_once_with(db, user_id)


def test_synthesize_returns_error_when_run_synthesis_raises(db, user_id):
    from syke.memory.synthesis import synthesize

    with patch(
        "syke.memory.synthesis._run_synthesis",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = synthesize(db, user_id, force=True)

    assert result["status"] == "error"
    assert result["error"] == "boom"


def test_run_synthesis_updates_memex_and_logs_op(db, user_id):
    from syke.memory.synthesis import _run_synthesis

    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    class FakeResultMessage:
        def __init__(self, total_cost_usd, num_turns):
            self.total_cost_usd = total_cost_usd
            self.num_turns = num_turns

    assistant_msg = FakeAssistantMessage(
        [FakeTextBlock("<memex>Updated memex</memex>")]
    )
    result_msg = FakeResultMessage(total_cost_usd=0.05, num_turns=3)

    async def fake_responses():
        yield assistant_msg
        yield result_msg

    mock_client = MagicMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = MagicMock(return_value=fake_responses())

    mock_sdk = MagicMock()
    mock_sdk.__aenter__ = AsyncMock(return_value=mock_client)
    mock_sdk.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "syke.memory.synthesis.build_memory_mcp_server", return_value=MagicMock()
        ),
        patch(
            "syke.memory.synthesis.ClaudeAgentOptions",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", FakeResultMessage),
        patch("syke.memory.synthesis.TextBlock", FakeTextBlock),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result == {
        "status": "ok",
        "cost_usd": 0.05,
        "num_turns": 3,
        "memex_updated": True,
    }
    assert db.get_memex(user_id)["content"] == "Updated memex"

    ops = db.get_memory_ops(user_id, limit=10)
    assert len(ops) >= 1
    assert ops[0]["operation"] == "synthesize"


def test_run_synthesis_without_memex_tags_does_not_update_memex(db, user_id):
    from syke.memory.synthesis import _run_synthesis

    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    class FakeResultMessage:
        def __init__(self, total_cost_usd, num_turns):
            self.total_cost_usd = total_cost_usd
            self.num_turns = num_turns

    assistant_msg = FakeAssistantMessage([FakeTextBlock("No memex tags here")])
    result_msg = FakeResultMessage(total_cost_usd=0.05, num_turns=3)

    async def fake_responses():
        yield assistant_msg
        yield result_msg

    mock_client = MagicMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = MagicMock(return_value=fake_responses())

    mock_sdk = MagicMock()
    mock_sdk.__aenter__ = AsyncMock(return_value=mock_client)
    mock_sdk.__aexit__ = AsyncMock(return_value=None)

    with (
        patch(
            "syke.memory.synthesis.build_memory_mcp_server", return_value=MagicMock()
        ),
        patch(
            "syke.memory.synthesis.ClaudeAgentOptions",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", FakeResultMessage),
        patch("syke.memory.synthesis.TextBlock", FakeTextBlock),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result == {
        "status": "ok",
        "cost_usd": 0.05,
        "num_turns": 3,
        "memex_updated": False,
    }
    assert db.get_memex(user_id) is None
