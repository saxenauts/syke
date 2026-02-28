from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Protocol, cast

from syke.db import SykeDB
from syke.memory.tools import (
    CONTENT_PREVIEW_LEN,
    _format_event,
    _format_memory,
    build_memory_mcp_server,
    create_memory_tools,
)
from syke.models import Event, Memory


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


def test_memory_chain_from_any_node(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-a", user_id=user_id, content="A"))
    db.supersede_memory(user_id, "m-a", Memory(id="m-b", user_id=user_id, content="B"))
    db.supersede_memory(user_id, "m-b", Memory(id="m-c", user_id=user_id, content="C"))
    assert [m["id"] for m in db.get_memory_chain(user_id, "m-a")] == [
        "m-a",
        "m-b",
        "m-c",
    ]
    assert [m["id"] for m in db.get_memory_chain(user_id, "m-b")] == [
        "m-a",
        "m-b",
        "m-c",
    ]
    assert db.get_memory_chain(user_id, "missing") == []


def test_update_memory_tool_success_and_missing(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-upd", user_id=user_id, content="before"))
    tool = _tool_by_name(_tools(db, user_id), "update_memory")
    ok = _run_tool(tool, {"memory_id": "m-upd", "new_content": "after"})
    missing = _run_tool(tool, {"memory_id": "missing", "new_content": "noop"})
    updated = db.get_memory(user_id, "m-upd")
    assert ok["status"] == "updated"
    assert updated is not None
    assert updated["content"] == "after"
    assert missing["status"] == "error"


def test_supersede_memory_tool_success_and_missing(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-old", user_id=user_id, content="old"))
    tool = _tool_by_name(_tools(db, user_id), "supersede_memory")
    ok = _run_tool(tool, {"memory_id": "m-old", "new_content": "new"})
    missing = _run_tool(tool, {"memory_id": "missing", "new_content": "new"})
    new_id = cast(str, ok["new_id"])
    old_mem = db.get_memory(user_id, "m-old")
    new_mem = db.get_memory(user_id, new_id)
    assert ok["status"] == "superseded"
    assert old_mem is not None
    assert new_mem is not None
    assert old_mem["active"] == 0
    assert new_mem["active"] == 1
    assert missing["status"] == "error"


def test_deactivate_memory_tool_success_and_inactive(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-deact", user_id=user_id, content="x"))
    tool = _tool_by_name(_tools(db, user_id), "deactivate_memory")
    first = _run_tool(tool, {"memory_id": "m-deact"})
    second = _run_tool(tool, {"memory_id": "m-deact"})
    assert first["status"] == "deactivated"
    assert second["status"] == "error"


def test_get_memory_tool_found_and_missing(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-get", user_id=user_id, content="Fetch me"))
    tool = _tool_by_name(_tools(db, user_id), "get_memory")
    found = _run_tool(tool, {"memory_id": "m-get"})
    missing = _run_tool(tool, {"memory_id": "missing"})
    assert found["status"] == "found"
    assert cast(dict[str, object], found["memory"])["id"] == "m-get"
    assert missing["status"] == "not_found"


def test_list_active_memories_tool_compact_index(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m1", user_id=user_id, content="Headline\nDetails"))
    db.insert_memory(Memory(id="m2", user_id=user_id, content="Another memory"))
    tool = _tool_by_name(_tools(db, user_id), "list_active_memories")
    data = _run_tool(tool, {"limit": 10})
    assert data["count"] == 2
    idx = cast(list[dict[str, object]], data["memories"])
    ids = {cast(str, row["id"]) for row in idx}
    assert ids == {"m1", "m2"}
    assert any(cast(str, row["first_line"]) == "Headline" for row in idx)


def test_get_memory_history_tool_found_and_missing(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="h-a", user_id=user_id, content="A"))
    db.supersede_memory(user_id, "h-a", Memory(id="h-b", user_id=user_id, content="B"))
    tool = _tool_by_name(_tools(db, user_id), "get_memory_history")
    found = _run_tool(tool, {"memory_id": "h-b"})
    missing = _run_tool(tool, {"memory_id": "missing"})
    assert found["versions"] == 2
    assert [
        cast(str, m["id"]) for m in cast(list[dict[str, object]], found["chain"])
    ] == ["h-a", "h-b"]
    assert missing["status"] == "not_found"


def test_mutation_tools_log_memory_ops(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)

    create_memory = _tool_by_name(tools, "create_memory")
    create_link = _tool_by_name(tools, "create_link")
    update_memory = _tool_by_name(tools, "update_memory")
    supersede_memory = _tool_by_name(tools, "supersede_memory")
    deactivate_memory = _tool_by_name(tools, "deactivate_memory")

    created = _run_tool(create_memory, {"content": "new insight"})
    mem_id = cast(str, created["memory_id"])
    db.insert_memory(Memory(id="other", user_id=user_id, content="other memory"))
    _ = _run_tool(
        create_link, {"source_id": mem_id, "target_id": "other", "reason": "related"}
    )
    _ = _run_tool(update_memory, {"memory_id": mem_id, "new_content": "updated"})
    superseded = _run_tool(
        supersede_memory, {"memory_id": mem_id, "new_content": "replacement"}
    )
    replacement_id = cast(str, superseded["new_id"])
    _ = _run_tool(deactivate_memory, {"memory_id": replacement_id})

    assert len(db.get_memory_ops(user_id, operation="add")) == 1
    assert len(db.get_memory_ops(user_id, operation="link")) == 1
    assert len(db.get_memory_ops(user_id, operation="update")) == 1
    assert len(db.get_memory_ops(user_id, operation="supersede")) == 1
    assert len(db.get_memory_ops(user_id, operation="deactivate")) == 1


def test_search_tools_for_memories_and_events(db: SykeDB, user_id: str) -> None:
    db.insert_memory(
        Memory(id="sm-1", user_id=user_id, content="Syke is an agentic memory layer")
    )
    db.insert_memory(Memory(id="sm-2", user_id=user_id, content="Python programming"))
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
    tools = _tools(db, user_id)
    search_memories = _tool_by_name(tools, "search_memories")
    search_evidence = _tool_by_name(tools, "search_evidence")
    mem_hits = _run_tool(search_memories, {"query": "agentic memory"})
    ev_hits = _run_tool(search_evidence, {"query": "auth"})
    assert cast(int, mem_hits["count"]) >= 1
    assert cast(int, ev_hits["count"]) >= 1


def test_link_navigation_and_cross_reference(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="a", user_id=user_id, content="Auth refactor"))
    db.insert_memory(Memory(id="b", user_id=user_id, content="Review notes"))
    create_link = _tool_by_name(_tools(db, user_id), "create_link")
    _ = _run_tool(
        create_link, {"source_id": "a", "target_id": "b", "reason": "Related"}
    )

    db.insert_event(
        Event(
            user_id=user_id,
            source="github",
            timestamp=datetime(2025, 2, 1),
            event_type="commit",
            title="Auth refactor",
            content="JWT auth module",
        )
    )
    db.insert_event(
        Event(
            user_id=user_id,
            source="gmail",
            timestamp=datetime(2025, 2, 2),
            event_type="email",
            title="Auth review",
            content="Review auth changes",
        )
    )

    tools = _tools(db, user_id)
    follow_links = _tool_by_name(tools, "follow_links")
    cross_reference = _tool_by_name(tools, "cross_reference")

    linked = _run_tool(follow_links, {"memory_id": "a"})
    cross = _run_tool(cross_reference, {"topic": "auth"})

    assert linked["count"] == 1
    assert cast(list[dict[str, object]], linked["linked_memories"])[0]["id"] == "b"
    assert cast(int, cross["total_matches"]) >= 2
    assert "github" in cast(list[str], cross["sources_with_matches"])
    assert "gmail" in cast(list[str], cross["sources_with_matches"])


def test_recent_memories_and_memex_tools(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    get_recent = _tool_by_name(tools, "get_recent_memories")
    get_memex = _tool_by_name(tools, "get_memex")

    empty_recent = _run_tool(get_recent, {})
    empty_memex = _run_tool(get_memex, {})
    assert empty_recent["count"] == 0
    assert empty_memex["exists"] is False

    for i in range(3):
        db.insert_memory(Memory(id=f"rec-{i}", user_id=user_id, content=f"Memory {i}"))
    db.insert_memory(
        Memory(
            id="memex-1",
            user_id=user_id,
            content="# Memex",
            source_event_ids=["__memex__"],
        )
    )

    recent = _run_tool(get_recent, {"limit": 2})
    memex = _run_tool(get_memex, {})
    assert recent["count"] == 2
    assert memex["exists"] is True


def test_browse_timeline_source_filter(db: SykeDB, user_id: str) -> None:
    db.insert_event(
        Event(
            user_id=user_id,
            source="github",
            timestamp=datetime(2025, 2, 1),
            event_type="commit",
            title="Morning commit",
            content="Fixed tests",
        )
    )
    db.insert_event(
        Event(
            user_id=user_id,
            source="gmail",
            timestamp=datetime(2025, 2, 2),
            event_type="email",
            title="Evening email",
            content="Review request",
        )
    )

    browse = _tool_by_name(_tools(db, user_id), "browse_timeline")
    all_events = _run_tool(browse, {})
    github_events = _run_tool(browse, {"source": "github"})
    assert all_events["count"] == 2
    assert github_events["count"] == 1
    assert (
        cast(list[dict[str, object]], github_events["events"])[0]["source"] == "github"
    )


def test_format_helpers_and_mcp_server(db: SykeDB, user_id: str) -> None:
    mem = {
        "id": "m1",
        "content": "x" * 2000,
        "created_at": "2025-02-01T10:00:00+00:00",
        "updated_at": None,
        "active": 1,
        "source_event_ids": "[]",
    }
    ev = {
        "id": "e1",
        "timestamp": "2025-02-01T10:00:00+00:00",
        "source": "github",
        "event_type": "commit",
        "title": "Test",
        "content": "y" * 2000,
    }

    fm = _format_memory(mem)
    fe = _format_event(ev)
    server = build_memory_mcp_server(db, user_id)

    assert len(cast(str, fm["content"])) == CONTENT_PREVIEW_LEN
    assert len(cast(str, fe["content_preview"])) == CONTENT_PREVIEW_LEN
    assert "local_created_at" in fm
    assert "local_time" in fe
    assert server is not None
