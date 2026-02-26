from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Protocol, cast

from syke.db import SykeDB
from syke.memory.tools import create_memory_tools
from syke.models import Event, Link, Memory

ToolResult = dict[str, object]


class ToolFn(Protocol):
    name: str
    handler: Callable[[dict[str, object]], Coroutine[object, object, ToolResult]]


def _tool_by_name(tools: list[ToolFn], name: str) -> ToolFn:
    return next(tool_fn for tool_fn in tools if tool_fn.name == name)


def _run_tool(tool_fn: ToolFn, args: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(tool_fn.handler(args))
    content = cast(list[dict[str, object]], result["content"])
    text = cast(str, content[0]["text"])
    return cast(dict[str, object], json.loads(text))


def _as_str(value: object) -> str:
    assert isinstance(value, str)
    return value


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _as_list_of_dicts(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [cast(dict[str, object], item) for item in value]


def _tools(db: SykeDB, user_id: str) -> list[ToolFn]:
    return cast(list[ToolFn], create_memory_tools(db, user_id))


def test_get_memory_chain_single(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-single", user_id=user_id, content="Only version"))

    chain = db.get_memory_chain(user_id, "m-single")

    assert [m["id"] for m in chain] == ["m-single"]


def test_get_memory_chain_multi(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-a", user_id=user_id, content="Version A"))
    db.supersede_memory(
        user_id, "m-a", Memory(id="m-b", user_id=user_id, content="Version B")
    )
    db.supersede_memory(
        user_id, "m-b", Memory(id="m-c", user_id=user_id, content="Version C")
    )

    chain_from_a = db.get_memory_chain(user_id, "m-a")
    chain_from_b = db.get_memory_chain(user_id, "m-b")
    chain_from_c = db.get_memory_chain(user_id, "m-c")

    expected = ["m-a", "m-b", "m-c"]
    assert [m["id"] for m in chain_from_a] == expected
    assert [m["id"] for m in chain_from_b] == expected
    assert [m["id"] for m in chain_from_c] == expected


def test_get_memory_chain_not_found(db: SykeDB, user_id: str) -> None:
    assert db.get_memory_chain(user_id, "missing-memory") == []


def test_update_memory_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-upd", user_id=user_id, content="Before update"))
    tools = _tools(db, user_id)
    update_memory = _tool_by_name(tools, "update_memory")

    data = _run_tool(
        update_memory, {"memory_id": "m-upd", "new_content": "After update"}
    )

    assert data["status"] == "updated"
    assert data["memory_id"] == "m-upd"
    updated = db.get_memory(user_id, "m-upd")
    assert updated is not None
    assert updated["content"] == "After update"


def test_update_memory_tool_not_found(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    update_memory = _tool_by_name(tools, "update_memory")

    data = _run_tool(update_memory, {"memory_id": "missing", "new_content": "No-op"})

    assert data["status"] == "error"
    assert "not found or inactive" in _as_str(data["error"])


def test_supersede_memory_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-old", user_id=user_id, content="Old content"))
    tools = _tools(db, user_id)
    supersede_memory = _tool_by_name(tools, "supersede_memory")

    data = _run_tool(
        supersede_memory,
        {"memory_id": "m-old", "new_content": "New content"},
    )

    assert data["status"] == "superseded"
    assert data["old_id"] == "m-old"
    old_mem = db.get_memory(user_id, "m-old")
    new_id = _as_str(data["new_id"])
    new_mem = db.get_memory(user_id, new_id)
    assert old_mem is not None
    assert new_mem is not None
    assert old_mem["active"] == 0
    assert old_mem["superseded_by"] == new_id
    assert new_mem["active"] == 1
    assert new_mem["content"] == "New content"


def test_supersede_memory_tool_not_found(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    supersede_memory = _tool_by_name(tools, "supersede_memory")

    data = _run_tool(
        supersede_memory,
        {"memory_id": "missing", "new_content": "Replacement"},
    )

    assert data["status"] == "error"
    assert "not found or inactive" in _as_str(data["error"])


def test_deactivate_memory_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-deact", user_id=user_id, content="Deactivate me"))
    tools = _tools(db, user_id)
    deactivate_memory = _tool_by_name(tools, "deactivate_memory")

    data = _run_tool(deactivate_memory, {"memory_id": "m-deact"})

    assert data["status"] == "deactivated"
    deactivated = db.get_memory(user_id, "m-deact")
    assert deactivated is not None
    assert deactivated["active"] == 0


def test_deactivate_memory_tool_already_inactive(db: SykeDB, user_id: str) -> None:
    db.insert_memory(
        Memory(id="m-inactive", user_id=user_id, content="Already inactive soon")
    )
    tools = _tools(db, user_id)
    deactivate_memory = _tool_by_name(tools, "deactivate_memory")

    first = _run_tool(deactivate_memory, {"memory_id": "m-inactive"})
    second = _run_tool(deactivate_memory, {"memory_id": "m-inactive"})

    assert first["status"] == "deactivated"
    assert second["status"] == "error"
    assert "not found or already inactive" in _as_str(second["error"])


def test_get_memory_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-get", user_id=user_id, content="Fetch me"))
    tools = _tools(db, user_id)
    get_memory = _tool_by_name(tools, "get_memory")

    data = _run_tool(get_memory, {"memory_id": "m-get"})

    assert data["status"] == "found"
    memory = _as_dict(data["memory"])
    assert memory["id"] == "m-get"
    assert memory["content"] == "Fetch me"


def test_get_memory_tool_not_found(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    get_memory = _tool_by_name(tools, "get_memory")

    data = _run_tool(get_memory, {"memory_id": "missing"})

    assert data["status"] == "not_found"
    assert data["memory_id"] == "missing"


def test_list_active_memories_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-list-1", user_id=user_id, content="First line only"))
    db.insert_memory(
        Memory(
            id="m-list-2",
            user_id=user_id,
            content="Headline\nDetail line that should not appear",
        )
    )
    db.insert_memory(Memory(id="m-list-3", user_id=user_id, content="Another memory"))

    tools = _tools(db, user_id)
    list_active_memories = _tool_by_name(tools, "list_active_memories")
    data = _run_tool(list_active_memories, {"limit": 10})

    assert data["count"] == 3
    memories = _as_list_of_dicts(data["memories"])
    by_id = {_as_str(m["id"]): m for m in memories}
    assert set(by_id.keys()) == {"m-list-1", "m-list-2", "m-list-3"}
    assert by_id["m-list-1"]["first_line"] == "First line only"
    assert by_id["m-list-2"]["first_line"] == "Headline"
    assert by_id["m-list-3"]["first_line"] == "Another memory"


def test_list_active_memories_empty(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    list_active_memories = _tool_by_name(tools, "list_active_memories")

    data = _run_tool(list_active_memories, {})

    assert data["count"] == 0
    assert data["memories"] == []


def test_get_memory_history_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-h-a", user_id=user_id, content="Version A"))
    db.supersede_memory(
        user_id, "m-h-a", Memory(id="m-h-b", user_id=user_id, content="Version B")
    )
    db.supersede_memory(
        user_id, "m-h-b", Memory(id="m-h-c", user_id=user_id, content="Version C")
    )

    tools = _tools(db, user_id)
    get_memory_history = _tool_by_name(tools, "get_memory_history")
    data = _run_tool(get_memory_history, {"memory_id": "m-h-b"})

    assert data["versions"] == 3
    chain = _as_list_of_dicts(data["chain"])
    assert [_as_str(m["id"]) for m in chain] == ["m-h-a", "m-h-b", "m-h-c"]


def test_get_memory_history_tool_not_found(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    get_memory_history = _tool_by_name(tools, "get_memory_history")

    data = _run_tool(get_memory_history, {"memory_id": "missing"})

    assert data["status"] == "not_found"
    assert data["memory_id"] == "missing"


def test_update_logs_memory_op(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-op-upd", user_id=user_id, content="Before"))
    tools = _tools(db, user_id)
    update_memory = _tool_by_name(tools, "update_memory")

    data = _run_tool(update_memory, {"memory_id": "m-op-upd", "new_content": "After"})

    assert data["status"] == "updated"
    ops = db.get_memory_ops(user_id, operation="update")
    assert len(ops) == 1
    assert ops[0]["operation"] == "update"
    assert "m-op-upd" in ops[0]["memory_ids"]


def test_supersede_logs_memory_op(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-op-old", user_id=user_id, content="Old"))
    tools = _tools(db, user_id)
    supersede_memory = _tool_by_name(tools, "supersede_memory")

    data = _run_tool(
        supersede_memory,
        {"memory_id": "m-op-old", "new_content": "New"},
    )

    assert data["status"] == "superseded"
    ops = db.get_memory_ops(user_id, operation="supersede")
    assert len(ops) == 1
    assert ops[0]["operation"] == "supersede"
    assert "m-op-old" in ops[0]["memory_ids"]
    assert data["new_id"] in ops[0]["memory_ids"]


def test_deactivate_logs_memory_op(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="m-op-deact", user_id=user_id, content="Soon inactive"))
    tools = _tools(db, user_id)
    deactivate_memory = _tool_by_name(tools, "deactivate_memory")

    data = _run_tool(deactivate_memory, {"memory_id": "m-op-deact"})

    assert data["status"] == "deactivated"
    ops = db.get_memory_ops(user_id, operation="deactivate")
    assert len(ops) == 1
    assert ops[0]["operation"] == "deactivate"
    assert "m-op-deact" in ops[0]["memory_ids"]


# ---------------------------------------------------------------------------
# Read tools (search, follow links, browse, cross-reference, get_memex)
# ---------------------------------------------------------------------------


def test_search_memories_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="sm-1", user_id=user_id, content="Syke is an agentic memory layer"))
    db.insert_memory(Memory(id="sm-2", user_id=user_id, content="Python programming basics"))
    tools = _tools(db, user_id)
    search_memories = _tool_by_name(tools, "search_memories")

    data = _run_tool(search_memories, {"query": "agentic memory"})

    assert data["query"] == "agentic memory"
    assert data["count"] >= 1
    ids = {_as_str(m["id"]) for m in _as_list_of_dicts(data["memories"])}
    assert "sm-1" in ids


def test_search_evidence_tool(db: SykeDB, user_id: str) -> None:
    from datetime import datetime
    db.insert_event(Event(
        user_id=user_id, source="github", timestamp=datetime(2025, 2, 1),
        event_type="commit", title="Refactor auth module", content="JWT tokens replaced"
    ))
    tools = _tools(db, user_id)
    search_evidence = _tool_by_name(tools, "search_evidence")

    data = _run_tool(search_evidence, {"query": "auth"})

    assert data["query"] == "auth"
    assert data["count"] >= 1
    assert any("Refactor auth" in _as_str(ev.get("title", "")) for ev in _as_list_of_dicts(data["events"]))


def test_follow_links_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="fl-a", user_id=user_id, content="Memory A"))
    db.insert_memory(Memory(id="fl-b", user_id=user_id, content="Memory B"))
    db.insert_link(Link(id="fl-link", user_id=user_id, source_id="fl-a", target_id="fl-b", reason="Related"))
    tools = _tools(db, user_id)
    follow_links = _tool_by_name(tools, "follow_links")

    data = _run_tool(follow_links, {"memory_id": "fl-a"})

    assert data["memory_id"] == "fl-a"
    assert data["count"] == 1
    linked = _as_list_of_dicts(data["linked_memories"])
    assert linked[0]["id"] == "fl-b"
    assert linked[0]["link_reason"] == "Related"


def test_follow_links_tool_empty(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="fl-solo", user_id=user_id, content="No links"))
    tools = _tools(db, user_id)
    follow_links = _tool_by_name(tools, "follow_links")

    data = _run_tool(follow_links, {"memory_id": "fl-solo"})

    assert data["count"] == 0
    assert data["linked_memories"] == []


def test_create_memory_tool(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    create_memory = _tool_by_name(tools, "create_memory")

    data = _run_tool(create_memory, {"content": "New insight about the user"})

    assert data["status"] == "created"
    new_id = _as_str(data["memory_id"])
    mem = db.get_memory(user_id, new_id)
    assert mem is not None
    assert mem["content"] == "New insight about the user"
    ops = db.get_memory_ops(user_id, operation="add")
    assert len(ops) == 1


def test_create_link_tool(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="cl-a", user_id=user_id, content="Memory A"))
    db.insert_memory(Memory(id="cl-b", user_id=user_id, content="Memory B"))
    tools = _tools(db, user_id)
    create_link = _tool_by_name(tools, "create_link")

    data = _run_tool(create_link, {"source_id": "cl-a", "target_id": "cl-b", "reason": "Connected topics"})

    assert data["status"] == "linked"
    link_id = _as_str(data["link_id"])
    assert len(link_id) > 0
    links = db.get_links_for(user_id, "cl-a")
    assert len(links) == 1
    ops = db.get_memory_ops(user_id, operation="link")
    assert len(ops) == 1


def test_get_recent_memories_tool(db: SykeDB, user_id: str) -> None:
    for i in range(3):
        db.insert_memory(Memory(id=f"rec-{i}", user_id=user_id, content=f"Memory {i}"))
    tools = _tools(db, user_id)
    get_recent = _tool_by_name(tools, "get_recent_memories")

    data = _run_tool(get_recent, {"limit": 2})

    assert data["count"] == 2


def test_get_recent_memories_tool_empty(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    get_recent = _tool_by_name(tools, "get_recent_memories")

    data = _run_tool(get_recent, {})

    assert data["count"] == 0
    assert data["memories"] == []


def test_get_memex_tool_exists(db: SykeDB, user_id: str) -> None:
    db.insert_memory(Memory(id="memex-t", user_id=user_id, content="# Memex map", source_event_ids=["__memex__"]))
    tools = _tools(db, user_id)
    get_memex = _tool_by_name(tools, "get_memex")

    data = _run_tool(get_memex, {})

    assert data["exists"] is True
    memex = _as_dict(data["memex"])
    assert memex["id"] == "memex-t"


def test_get_memex_tool_not_exists(db: SykeDB, user_id: str) -> None:
    tools = _tools(db, user_id)
    get_memex = _tool_by_name(tools, "get_memex")

    data = _run_tool(get_memex, {})

    assert data["exists"] is False
    assert "hint" in data


def test_browse_timeline_tool(db: SykeDB, user_id: str) -> None:
    from datetime import datetime
    db.insert_event(Event(
        user_id=user_id, source="github", timestamp=datetime(2025, 2, 1),
        event_type="commit", title="Morning commit", content="Fixed tests"
    ))
    db.insert_event(Event(
        user_id=user_id, source="gmail", timestamp=datetime(2025, 2, 2),
        event_type="email", title="Evening email", content="Review request"
    ))
    tools = _tools(db, user_id)
    browse = _tool_by_name(tools, "browse_timeline")

    data = _run_tool(browse, {})
    assert data["count"] == 2

    # Filter by source
    data_github = _run_tool(browse, {"source": "github"})
    assert data_github["count"] == 1
    assert _as_list_of_dicts(data_github["events"])[0]["source"] == "github"


def test_cross_reference_tool(db: SykeDB, user_id: str) -> None:
    from datetime import datetime
    db.insert_event(Event(
        user_id=user_id, source="github", timestamp=datetime(2025, 2, 1),
        event_type="commit", title="Auth refactor", content="JWT auth module"
    ))
    db.insert_event(Event(
        user_id=user_id, source="gmail", timestamp=datetime(2025, 2, 2),
        event_type="email", title="Auth review", content="Review the auth changes"
    ))
    tools = _tools(db, user_id)
    cross_ref = _tool_by_name(tools, "cross_reference")

    data = _run_tool(cross_ref, {"topic": "auth"})

    assert data["topic"] == "auth"
    assert data["total_matches"] >= 2
    assert "github" in data["sources_with_matches"]
    assert "gmail" in data["sources_with_matches"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_format_memory_truncates_content() -> None:
    from syke.memory.tools import _format_memory, CONTENT_PREVIEW_LEN

    long_content = "x" * 2000
    mem = {"id": "m1", "content": long_content, "created_at": "", "updated_at": None, "active": 1, "source_event_ids": "[]"}
    formatted = _format_memory(mem)
    assert len(formatted["content"]) == CONTENT_PREVIEW_LEN


def test_format_event_truncates_content() -> None:
    from syke.memory.tools import _format_event, CONTENT_PREVIEW_LEN

    long_content = "y" * 2000
    ev = {"id": "e1", "timestamp": "2025-02-01", "source": "github", "event_type": "commit", "title": "Test", "content": long_content}
    formatted = _format_event(ev)
    assert len(formatted["content_preview"]) == CONTENT_PREVIEW_LEN


def test_build_memory_mcp_server(db: SykeDB, user_id: str) -> None:
    from syke.memory.tools import build_memory_mcp_server

    server = build_memory_mcp_server(db, user_id)
    assert server is not None
