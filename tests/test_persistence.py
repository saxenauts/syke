"""Tests for the persistence layer — DB, memory CRUD, FTS, links, memex, tools, synthesis."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from syke.db import SykeDB
from syke.memory.tools import (
    create_memory_tools,
)
from syke.models import Event, Link, Memory, UserProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ToolResult = dict[str, object]


class ToolFn(Protocol):
    name: str
    handler: Callable[[dict[str, object]], Coroutine[object, object, ToolResult]]


def _tools(db: SykeDB, user_id: str) -> list[ToolFn]:
    return cast(list[ToolFn], create_memory_tools(db, user_id))


def _tool_by_name(tools: list[ToolFn], name: str) -> ToolFn:
    return next(t for t in tools if t.name == name)


def _run_tool(tool_fn: ToolFn, args: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(tool_fn.handler(args))
    content = cast(list[dict[str, object]], result["content"])
    text = cast(str, content[0]["text"])
    return cast(dict[str, object], json.loads(text))


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


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, content: list[object]) -> None:
        self.content = content


class _FakeResultMessage:
    def __init__(self, total_cost_usd: float, num_turns: int) -> None:
        self.total_cost_usd = total_cost_usd
        self.num_turns = num_turns


def _sample_profile(user_id: str) -> UserProfile:
    from syke.models import ActiveThread, VoicePattern as VoicePatterns

    return UserProfile(
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


# --- DB layer ---


def test_insert_and_query_event(db, user_id):
    event = _evt(user_id, title="Test Event", content="This is test content.")
    assert db.insert_event(event) is True
    events = db.get_events(user_id)
    assert len(events) == 1
    assert events[0]["title"] == "Test Event"


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
    [
        ("test_user", True),
        ("other_user", False),
    ],
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


def test_migration_idempotent(tmp_path):
    db = SykeDB(tmp_path / "idem.db")
    db.initialize()
    db.initialize()
    assert db.count_events("nobody") == 0
    db.close()


# --- Memory CRUD ---


def test_insert_and_get_memory(db, user_id):
    db.insert_memory(
        Memory(id="m1", user_id=user_id, content="Utkarsh loves AI agents")
    )
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


# --- FTS search ---


def test_search_memories_fts(db, user_id):
    db.insert_memory(
        Memory(id="s1", user_id=user_id, content="Syke is an agentic memory system")
    )
    db.insert_memory(Memory(id="s2", user_id=user_id, content="Python programming"))
    db.insert_memory(
        Memory(id="s3", user_id=user_id, content="Memory and identity are the same")
    )
    results = db.search_memories(user_id, "memory")
    ids = {r["id"] for r in results}
    assert "s1" in ids and "s3" in ids


def test_search_memories_excludes_inactive(db, user_id):
    db.insert_memory(
        Memory(id="act", user_id=user_id, content="Active memory about Syke")
    )
    db.insert_memory(
        Memory(id="inact", user_id=user_id, content="Inactive memory about Syke")
    )
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


# --- Links ---


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


# --- Memex ---


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
    assert (
        len(ops) == 1 and ops[0]["operation"] == "add" and ops[0]["duration_ms"] == 42
    )


def test_bootstrap_memex_from_profile(db, user_id):
    from syke.memory.memex import bootstrap_memex_from_profile

    _insert_profile_row(db, _sample_profile(user_id))
    new_id = bootstrap_memex_from_profile(db, user_id)
    assert new_id is not None
    memex = db.get_memex(user_id)
    assert memex is not None
    content = memex["content"]
    assert content.startswith(f"# Memex — {user_id}")
    for section in ("## Identity", "## What's Active", "## Context"):
        assert section in content


def test_get_memex_for_injection_auto_bootstraps(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    _insert_profile_row(db, _sample_profile(user_id))
    result = get_memex_for_injection(db, user_id)
    assert result.startswith(f"# Memex — {user_id}")


# --- Memory tools ---


def test_memory_mutation_tools_success_and_missing(db, user_id):
    tools = _tools(db, user_id)
    db.insert_memory(Memory(id="m-upd", user_id=user_id, content="before"))
    db.insert_memory(Memory(id="m-old", user_id=user_id, content="old"))

    update = _run_tool(
        _tool_by_name(tools, "update_memory"),
        {"memory_id": "m-upd", "new_content": "after"},
    )
    assert update["status"] == "updated"
    assert db.get_memory(user_id, "m-upd")["content"] == "after"
    assert (
        _run_tool(
            _tool_by_name(tools, "update_memory"),
            {"memory_id": "missing", "new_content": "noop"},
        )["status"]
        == "error"
    )

    supersede = _run_tool(
        _tool_by_name(tools, "supersede_memory"),
        {"memory_id": "m-old", "new_content": "new"},
    )
    assert supersede["status"] == "superseded"
    assert db.get_memory(user_id, "m-old")["active"] == 0
    assert (
        _run_tool(
            _tool_by_name(tools, "supersede_memory"),
            {"memory_id": "missing", "new_content": "x"},
        )["status"]
        == "error"
    )

    get_tool = _tool_by_name(tools, "get_memory")
    assert _run_tool(get_tool, {"memory_id": "m-upd"})["status"] == "found"
    assert _run_tool(get_tool, {"memory_id": "missing"})["status"] == "not_found"


def test_memory_history_tool(db, user_id):
    db.insert_memory(Memory(id="h-a", user_id=user_id, content="A"))
    db.supersede_memory(user_id, "h-a", Memory(id="h-b", user_id=user_id, content="B"))
    tool = _tool_by_name(_tools(db, user_id), "get_memory_history")
    assert _run_tool(tool, {"memory_id": "h-b"})["versions"] == 2
    assert _run_tool(tool, {"memory_id": "missing"})["status"] == "not_found"


def test_mutation_tools_log_ops(db, user_id):
    tools = _tools(db, user_id)
    created = _run_tool(_tool_by_name(tools, "create_memory"), {"content": "insight"})
    mem_id = cast(str, created["memory_id"])
    db.insert_memory(Memory(id="other", user_id=user_id, content="other"))
    _run_tool(
        _tool_by_name(tools, "create_link"),
        {"source_id": mem_id, "target_id": "other", "reason": "related"},
    )
    _run_tool(
        _tool_by_name(tools, "update_memory"),
        {"memory_id": mem_id, "new_content": "updated"},
    )
    superseded = _run_tool(
        _tool_by_name(tools, "supersede_memory"),
        {"memory_id": mem_id, "new_content": "replacement"},
    )
    _run_tool(
        _tool_by_name(tools, "deactivate_memory"),
        {"memory_id": cast(str, superseded["new_id"])},
    )
    for op in ("add", "link", "update", "supersede", "deactivate"):
        assert len(db.get_memory_ops(user_id, operation=op)) == 1


# --- Synthesis ---


@pytest.mark.parametrize("force,expected_status", [(False, "skipped"), (True, "ok")])
def test_synthesize_threshold_behavior(db, user_id, force, expected_status):
    from syke.memory.synthesis import synthesize

    if not force:
        assert synthesize(db, user_id, force=force) == {
            "status": "skipped",
            "reason": "below_threshold",
        }
        return

    expected = {
        "status": "ok",
        "cost_usd": 0.0,
        "num_turns": 0,
        "memex_updated": False,
    }
    with patch(
        "syke.memory.synthesis._run_synthesis", new=AsyncMock(return_value=expected)
    ):
        assert synthesize(db, user_id, force=force) == expected


def test_synthesize_error(db, user_id):
    from syke.memory.synthesis import synthesize

    with patch(
        "syke.memory.synthesis._run_synthesis",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = synthesize(db, user_id, force=True)
    assert result["status"] == "error" and result["error"] == "boom"


def test_run_synthesis_updates_memex(db, user_id):
    from syke.memory.synthesis import _run_synthesis

    assistant_msg = _FakeAssistantMessage(
        [_FakeTextBlock("<memex>Updated memex</memex>")]
    )
    result_msg = _FakeResultMessage(total_cost_usd=0.05, num_turns=3)

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
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.TextBlock", _FakeTextBlock),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result == {
        "status": "ok",
        "cost_usd": 0.05,
        "num_turns": 3,
        "memex_updated": True,
    }
    assert db.get_memex(user_id)["content"] == "Updated memex"
