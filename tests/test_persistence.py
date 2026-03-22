"""Tests for the persistence layer — DB, memory CRUD, FTS, links, memex, tools, synthesis."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from syke.db import SykeDB
from syke.memory.tools import (
    create_ask_tools,
    create_synthesis_tools,
)
from syke.models import Event, Link, Memory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ToolResult = dict[str, object]


class ToolFn(Protocol):
    name: str
    handler: Callable[[dict[str, object]], Coroutine[object, object, ToolResult]]


def _tools(db: SykeDB, user_id: str) -> list[ToolFn]:
    return cast(list[ToolFn], create_synthesis_tools(db, user_id) + create_ask_tools(db, user_id))


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


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, content: list[object]) -> None:
        self.content = content


class _FakeToolUseBlock:
    def __init__(self, name: str, input: dict[str, object]) -> None:
        self.name = name
        self.input = input


class _FakeResultMessage:
    def __init__(self, total_cost_usd: float, num_turns: int) -> None:
        self.total_cost_usd = total_cost_usd
        self.num_turns = num_turns


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


# --- FTS search ---


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
    assert len(ops) == 1 and ops[0]["operation"] == "add" and ops[0]["duration_ms"] == 42


def test_get_memex_for_injection_no_data_fallback(db, user_id):
    from syke.memory.memex import get_memex_for_injection

    # Fresh DB with no events and no memories
    result = get_memex_for_injection(db, user_id)
    assert result == "[No data yet.]"


# --- Memory tools ---


def test_get_memory_tool(db, user_id):
    db.insert_memory(Memory(id="m-get", user_id=user_id, content="test content"))
    tools = _tools(db, user_id)
    get_tool = _tool_by_name(tools, "get_memory")
    assert _run_tool(get_tool, {"memory_id": "m-get"})["status"] == "found"
    assert _run_tool(get_tool, {"memory_id": "missing"})["status"] == "not_found"


def test_memory_history_tool(db, user_id):
    db.insert_memory(Memory(id="h-a", user_id=user_id, content="A"))
    db.supersede_memory(user_id, "h-a", Memory(id="h-b", user_id=user_id, content="B"))
    tool = _tool_by_name(_tools(db, user_id), "get_memory_history")
    assert _run_tool(tool, {"memory_id": "h-b"})["versions"] == 2
    assert _run_tool(tool, {"memory_id": "missing"})["status"] == "not_found"


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
    with patch("syke.memory.synthesis._run_synthesis", new=AsyncMock(return_value=expected)):
        assert synthesize(db, user_id, force=force) == expected


def test_synthesize_error(db, user_id):
    from syke.memory.synthesis import synthesize

    with patch(
        "syke.memory.synthesis._run_synthesis",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = synthesize(db, user_id, force=True)
    assert result["status"] == "error" and result["error"] == "boom"


def test_should_synthesize_when_cursor_backlog_exists(db, user_id):
    from syke.memory.synthesis import _should_synthesize

    event_ids = _insert_events(db, user_id, 10)
    db.set_synthesis_cursor(user_id, event_ids[2])
    db.log_memory_op(user_id, "synthesize")

    assert db.count_events_since(user_id, cast(str, db.get_last_synthesis_timestamp(user_id))) == 0
    assert db.count_events_after_id(user_id, event_ids[2]) == 7
    assert _should_synthesize(db, user_id) is True


def test_should_synthesize_skips_small_new_batch_without_backlog(db, user_id):
    from syke.memory.synthesis import _should_synthesize

    event_ids = _insert_events(db, user_id, 5)
    db.set_synthesis_cursor(user_id, event_ids[-1])
    db.log_memory_op(user_id, "synthesize")
    _insert_events(db, user_id, 3, start=5)

    assert db.count_events_since(user_id, cast(str, db.get_last_synthesis_timestamp(user_id))) == 3
    assert _should_synthesize(db, user_id) is False


def test_get_new_events_summary_uses_cursor(db, user_id):
    from syke.memory.synthesis import _get_new_events_summary

    event_ids = _insert_events(db, user_id, 5)
    db.set_synthesis_cursor(user_id, event_ids[1])

    summary, new_cursor = _get_new_events_summary(db, user_id, limit=2)

    assert "Event 0" not in summary
    assert "Event 1" not in summary
    assert "Event 2" in summary
    assert "Event 3" in summary
    assert new_cursor == event_ids[3]


def test_run_synthesis_updates_memex(db, user_id):
    from syke.memory.synthesis import _run_synthesis

    event_ids = _insert_events(db, user_id, 1)
    assistant_msg = _FakeAssistantMessage(
        [_FakeToolUseBlock("commit_cycle", {"status": "completed", "content": "Updated memex"})]
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
        patch("syke.memory.synthesis.create_sdk_mcp_server", return_value=MagicMock()),
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.ToolUseBlock", _FakeToolUseBlock),
        patch(
            "syke.memory.synthesis.build_agent_env",
            return_value={"ANTHROPIC_API_KEY": ""},
        ),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result["status"] == "ok"
    assert result["cost_usd"] == 0.05
    assert result["num_turns"] == 3
    assert result["memex_updated"] is True
    assert "input_tokens" in result
    assert "output_tokens" in result
    assert db.get_memex(user_id)["content"] == "Updated memex"
    assert db.get_synthesis_cursor(user_id) == event_ids[0]


def test_run_synthesis_unchanged_memex(db, user_id):
    from syke.memory.memex import update_memex
    from syke.memory.synthesis import _run_synthesis

    update_memex(db, user_id, "Original memex")
    assistant_msg = _FakeAssistantMessage(
        [_FakeToolUseBlock("commit_cycle", {"status": "completed"})]
    )
    result_msg = _FakeResultMessage(total_cost_usd=0.02, num_turns=1)

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
        patch("syke.memory.synthesis.create_sdk_mcp_server", return_value=MagicMock()),
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.ToolUseBlock", _FakeToolUseBlock),
        patch(
            "syke.memory.synthesis.build_agent_env",
            return_value={"ANTHROPIC_API_KEY": ""},
        ),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result["status"] == "ok"
    assert result["cost_usd"] == 0.02
    assert result["num_turns"] == 1
    assert result["memex_updated"] is False
    assert db.get_memex(user_id)["content"] == "Original memex"


def test_run_synthesis_errors_when_not_finalized(db, user_id):
    from syke.memory.synthesis import _run_synthesis

    assistant_msg = _FakeAssistantMessage([_FakeTextBlock("No final tool call")])
    result_msg = _FakeResultMessage(total_cost_usd=0.01, num_turns=1)

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
        patch("syke.memory.synthesis.create_sdk_mcp_server", return_value=MagicMock()),
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.ToolUseBlock", _FakeToolUseBlock),
        patch(
            "syke.memory.synthesis.build_agent_env",
            return_value={"ANTHROPIC_API_KEY": ""},
        ),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result["status"] == "incomplete"
    assert "commit_cycle" in cast(str, result["error"])


def test_run_synthesis_empty_content_skips_memex_update(db, user_id):
    from syke.memory.synthesis import _run_synthesis

    assistant_msg = _FakeAssistantMessage(
        [_FakeToolUseBlock("commit_cycle", {"status": "completed", "content": "   "})]
    )
    result_msg = _FakeResultMessage(total_cost_usd=0.01, num_turns=1)

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
        patch("syke.memory.synthesis.create_sdk_mcp_server", return_value=MagicMock()),
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.ToolUseBlock", _FakeToolUseBlock),
        patch(
            "syke.memory.synthesis.build_agent_env",
            return_value={"ANTHROPIC_API_KEY": ""},
        ),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result["status"] == "ok"
    assert result["memex_updated"] is False


# --- memory_write unified dispatch tool ---


def test_memory_write_create(db, user_id):
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(
        mem_write, {"op": "create", "params": {"content": "Created via memory_write"}}
    )
    assert result["status"] == "created"
    mem_id = cast(str, result["memory_id"])
    mem = db.get_memory(user_id, mem_id)
    assert mem is not None
    assert mem["content"] == "Created via memory_write"
    assert mem["active"] == 1


def test_memory_write_update(db, user_id):
    db.insert_memory(Memory(id="m-write-upd", user_id=user_id, content="Original content"))
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(
        mem_write,
        {
            "op": "update",
            "params": {"memory_id": "m-write-upd", "new_content": "Updated via memory_write"},
        },
    )
    assert result["status"] == "updated"
    mem = db.get_memory(user_id, "m-write-upd")
    assert mem["content"] == "Updated via memory_write"


def test_memory_write_supersede(db, user_id):
    db.insert_memory(Memory(id="m-write-old", user_id=user_id, content="Old version"))
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(
        mem_write,
        {
            "op": "supersede",
            "params": {"memory_id": "m-write-old", "new_content": "New version via memory_write"},
        },
    )
    assert result["status"] == "superseded"
    assert result["old_id"] == "m-write-old"
    new_id = cast(str, result["new_id"])
    old_mem = db.get_memory(user_id, "m-write-old")
    new_mem = db.get_memory(user_id, new_id)
    assert old_mem["active"] == 0
    assert old_mem["superseded_by"] == new_id
    assert new_mem["active"] == 1
    assert new_mem["content"] == "New version via memory_write"


def test_memory_write_deactivate(db, user_id):
    db.insert_memory(Memory(id="m-write-deact", user_id=user_id, content="To deactivate"))
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(
        mem_write,
        {
            "op": "deactivate",
            "params": {"memory_id": "m-write-deact", "reason": "No longer relevant"},
        },
    )
    assert result["status"] == "deactivated"
    mem = db.get_memory(user_id, "m-write-deact")
    assert mem["active"] == 0


def test_memory_write_link(db, user_id):
    db.insert_memory(Memory(id="m-link-a", user_id=user_id, content="Memory A"))
    db.insert_memory(Memory(id="m-link-b", user_id=user_id, content="Memory B"))
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(
        mem_write,
        {
            "op": "link",
            "params": {
                "source_id": "m-link-a",
                "target_id": "m-link-b",
                "reason": "Related concepts",
            },
        },
    )
    assert result["status"] == "linked"
    link_id = cast(str, result["link_id"])
    linked = db.get_linked_memories(user_id, "m-link-a")
    assert len(linked) == 1
    assert linked[0]["id"] == "m-link-b"
    assert linked[0]["link_reason"] == "Related concepts"


def test_memory_write_invalid_op(db, user_id):
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(mem_write, {"op": "delete_everything", "params": {}})
    assert result["status"] == "error"
    assert "Invalid operation" in cast(str, result["error"])


def test_memory_write_missing_params(db, user_id):
    tools = _tools(db, user_id)
    mem_write = _tool_by_name(tools, "memory_write")
    result = _run_tool(mem_write, {"op": "create", "params": {}})
    assert result["status"] == "error"
    assert "content" in cast(str, result["error"])


# ---------------------------------------------------------------------------
# T1: Atomicity guards
# ---------------------------------------------------------------------------


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
            "SELECT * FROM memories WHERE user_id = ? AND id = ?", (user_id, mid)
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


# ---------------------------------------------------------------------------
# T2: Cycle records
# ---------------------------------------------------------------------------


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


def test_cycle_record_immutable(db, user_id):
    cid = db.insert_cycle_record(user_id, cursor_start="evt-1")
    original = db.get_cycle_records(user_id)[0]
    db._conn.execute(
        "UPDATE cycle_records SET cursor_start = 'tampered' WHERE id = ?",
        (cid,),
    )
    db._conn.commit()
    updated = db.get_cycle_records(user_id)[0]
    assert updated["cursor_start"] == "tampered"


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


# ---------------------------------------------------------------------------
# T6: commit_cycle tool tests
# ---------------------------------------------------------------------------


def test_commit_cycle_completed(db, user_id):
    cid = db.insert_cycle_record(user_id, cursor_start="evt-1", model="test-model")
    db.complete_cycle_record(
        cid,
        status="completed",
        cursor_end="evt-99",
        events_processed=5,
        memories_created=2,
        memories_updated=1,
        links_created=3,
        memex_updated=1,
    )
    records = db.get_cycle_records(user_id)
    assert len(records) == 1
    assert records[0]["status"] == "completed"
    assert records[0]["cursor_end"] == "evt-99"
    assert records[0]["events_processed"] == 5
    assert records[0]["memories_created"] == 2
    assert records[0]["memex_updated"] == 1


def test_commit_cycle_failed(db, user_id):
    cid = db.insert_cycle_record(user_id, cursor_start="evt-1")
    db.complete_cycle_record(cid, status="failed")
    records = db.get_cycle_records(user_id)
    assert records[0]["status"] == "failed"
    assert records[0]["completed_at"] is not None


def test_commit_cycle_advances_cursor(db, user_id):
    db.set_synthesis_cursor(user_id, "old-cursor")
    assert db.get_synthesis_cursor(user_id) == "old-cursor"
    db.set_synthesis_cursor(user_id, "new-cursor")
    assert db.get_synthesis_cursor(user_id) == "new-cursor"


def test_commit_cycle_writes_cycle_record(db, user_id):
    skill_hash = "a" * 64
    cid = db.insert_cycle_record(
        user_id, cursor_start="evt-1", skill_hash=skill_hash, model="claude-3-opus"
    )
    records = db.get_cycle_records(user_id)
    assert len(records) == 1
    assert records[0]["id"] == cid
    assert records[0]["skill_hash"] == skill_hash
    assert records[0]["model"] == "claude-3-opus"


def test_commit_cycle_stores_hints(db, user_id):
    cid = db.insert_cycle_record(user_id)
    db.insert_cycle_annotation(cid, "synthesis", "hints", "some hint text for future cycles")
    rows = db._conn.execute(
        "SELECT * FROM cycle_annotations WHERE cycle_id = ? AND annotation_type = 'hints'",
        (cid,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["content"] == "some hint text for future cycles"


# ---------------------------------------------------------------------------
# T7: post-run failure detection tests
# ---------------------------------------------------------------------------


def test_missing_commit_cycle_detected(db, user_id):
    cid = db.insert_cycle_record(user_id, cursor_start="evt-1")
    db.complete_cycle_record(cid, status="incomplete")
    records = db.get_cycle_records(user_id)
    assert records[0]["status"] == "incomplete"


def test_incomplete_status_logged(db, user_id):
    cid = db.insert_cycle_record(user_id)
    db.complete_cycle_record(cid, status="incomplete")
    records = db.get_cycle_records(user_id)
    assert records[0]["completed_at"] is not None


# ---------------------------------------------------------------------------
# T8: skill file loading + SHA256 tests
# ---------------------------------------------------------------------------


def test_skill_file_loading():
    from syke.memory.synthesis import _load_skill_file

    content, skill_hash = _load_skill_file()
    assert isinstance(content, str)
    assert len(content) > 0
    assert isinstance(skill_hash, str)
    assert len(skill_hash) == 64


def test_skill_hash_computed():
    from syke.memory.synthesis import _load_skill_file

    _, hash1 = _load_skill_file()
    _, hash2 = _load_skill_file()
    assert hash1 == hash2
    assert len(hash1) == 64


def test_skill_hash_on_cycle_record(db, user_id):
    from syke.memory.synthesis import _load_skill_file

    _, skill_hash = _load_skill_file()
    cid = db.insert_cycle_record(user_id, skill_hash=skill_hash)
    records = db.get_cycle_records(user_id)
    assert records[0]["skill_hash"] == skill_hash


def test_skill_file_missing_fallback(monkeypatch):
    from syke.memory import synthesis

    original_path = synthesis._SKILL_FILE
    monkeypatch.setattr(synthesis, "_SKILL_FILE", synthesis._SKILL_DIR / "nonexistent.md")

    content, skill_hash = synthesis._load_skill_file()
    assert content == synthesis._FALLBACK_PROMPT
    assert len(skill_hash) == 64

    monkeypatch.setattr(synthesis, "_SKILL_FILE", original_path)


# ---------------------------------------------------------------------------
# T12: FTS5 sync triggers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T13: E2E integration test — full synthesis cycle
# ---------------------------------------------------------------------------


def test_e2e_synthesis_cycle(db, user_id):
    """Full cycle: events → memory_write(create) → commit_cycle(completed) → verify DB."""
    from syke.memory.synthesis import _run_synthesis

    event_ids = _insert_events(db, user_id, 3)

    assistant_msg = _FakeAssistantMessage(
        [
            _FakeToolUseBlock(
                "memory_write",
                {"op": "create", "params": {"content": "User prefers dark mode"}},
            ),
            _FakeToolUseBlock(
                "memory_write",
                {"op": "create", "params": {"content": "User works on Syke project"}},
            ),
            _FakeToolUseBlock(
                "commit_cycle",
                {
                    "status": "completed",
                    "content": "# Memex\n\n## Preferences\n- Dark mode\n\n## Projects\n- Syke",
                    "hints": "user seems to prefer minimal UI",
                },
            ),
        ]
    )
    result_msg = _FakeResultMessage(total_cost_usd=0.08, num_turns=5)

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
        patch("syke.memory.synthesis.create_sdk_mcp_server", return_value=MagicMock()),
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.ToolUseBlock", _FakeToolUseBlock),
        patch(
            "syke.memory.synthesis.build_agent_env",
            return_value={"ANTHROPIC_API_KEY": ""},
        ),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result["status"] == "ok"
    assert result["memex_updated"] is True
    assert result["cost_usd"] == 0.08
    assert result["num_turns"] == 5

    memex = db.get_memex(user_id)
    assert "Dark mode" in memex["content"]
    assert "Syke" in memex["content"]

    assert db.get_synthesis_cursor(user_id) == event_ids[-1]

    records = db.get_cycle_records(user_id)
    assert len(records) == 1
    assert records[0]["status"] == "completed"
    assert records[0]["cursor_end"] == event_ids[-1]
    assert records[0]["events_processed"] == 3


def test_e2e_synthesis_incomplete(db, user_id):
    """Agent doesn't call commit_cycle → status='incomplete', cycle_record marked."""
    from syke.memory.synthesis import _run_synthesis

    _insert_events(db, user_id, 2)

    assistant_msg = _FakeAssistantMessage(
        [_FakeToolUseBlock("memory_write", {"op": "create", "params": {"content": "orphan"}})]
    )
    result_msg = _FakeResultMessage(total_cost_usd=0.03, num_turns=2)

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
        patch("syke.memory.synthesis.create_sdk_mcp_server", return_value=MagicMock()),
        patch("syke.memory.synthesis.ClaudeAgentOptions", side_effect=lambda **kw: kw),
        patch("syke.memory.synthesis.ClaudeSDKClient", return_value=mock_sdk),
        patch("syke.memory.synthesis.AssistantMessage", _FakeAssistantMessage),
        patch("syke.memory.synthesis.ResultMessage", _FakeResultMessage),
        patch("syke.memory.synthesis.ToolUseBlock", _FakeToolUseBlock),
        patch(
            "syke.memory.synthesis.build_agent_env",
            return_value={"ANTHROPIC_API_KEY": ""},
        ),
    ):
        result = asyncio.run(_run_synthesis(db, user_id))

    assert result["status"] == "incomplete"
    assert "commit_cycle" in cast(str, result["error"])

    records = db.get_cycle_records(user_id)
    assert len(records) == 1
    assert records[0]["status"] == "incomplete"
