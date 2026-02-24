"""Memory tools for the ask() agent — search, navigate, create, link."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from syke.db import SykeDB
from syke.models import Link, Memory

from uuid_extensions import uuid7

MEMORY_TOOL_NAMES = [
    "search_memories",
    "search_evidence",
    "follow_links",
    "create_memory",
    "create_link",
    "get_recent_memories",
    "get_memex",
    "browse_timeline",
    "cross_reference",
    "update_memory",
    "supersede_memory",
    "deactivate_memory",
    "get_memory",
    "list_active_memories",
    "get_memory_history",
]

CONTENT_PREVIEW_LEN = 1200


def _format_memory(mem: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": mem["id"],
        "content": (mem.get("content") or "")[:CONTENT_PREVIEW_LEN],
        "created_at": mem.get("created_at", ""),
        "updated_at": mem.get("updated_at"),
        "active": mem.get("active", 1),
        "source_event_ids": mem.get("source_event_ids", "[]"),
    }


def _format_event(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ev["id"],
        "timestamp": ev["timestamp"],
        "source": ev["source"],
        "event_type": ev["event_type"],
        "title": ev.get("title") or "",
        "content_preview": (ev.get("content") or "")[:CONTENT_PREVIEW_LEN],
    }


def create_memory_tools(db: SykeDB, user_id: str) -> list:
    """Build memory tools bound to a specific DB and user.

    These tools let the ask() agent navigate the memory layer:
    memex -> memories -> evidence, and create new memories/links
    from what it discovers.
    """

    @tool(
        "search_memories",
        "BM25 full-text search over the memory layer. Returns memories ranked by relevance.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "integer", "description": "Max results (default 15)"},
            },
            "required": ["query"],
        },
    )
    async def search_memories(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 15), 50)
        results = db.search_memories(user_id, args["query"], limit=limit)
        formatted = [_format_memory(m) for m in results]

        result = {
            "query": args["query"],
            "count": len(formatted),
            "memories": formatted,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "search_evidence",
        "BM25 full-text search over raw events. Use when memories don't have the answer.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "integer", "description": "Max results (default 15)"},
            },
            "required": ["query"],
        },
    )
    async def search_evidence(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 15), 50)
        results = db.search_events_fts(user_id, args["query"], limit=limit)
        formatted = [_format_event(ev) for ev in results]

        result = {"query": args["query"], "count": len(formatted), "events": formatted}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "follow_links",
        "Get all memories linked to a given memory, with the natural language reason for each link.",
        {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to follow links from",
                },
            },
            "required": ["memory_id"],
        },
    )
    async def follow_links(args: dict[str, Any]) -> dict[str, Any]:
        memory_id = args["memory_id"]
        linked = db.get_linked_memories(user_id, memory_id)
        formatted = []
        for m in linked:
            entry = _format_memory(m)
            entry["link_reason"] = m.get("link_reason", "")
            entry["link_id"] = m.get("link_id", "")
            formatted.append(entry)
        result = {
            "memory_id": memory_id,
            "count": len(formatted),
            "linked_memories": formatted,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "create_memory",
        "Create a new memory from what you've learned. Use this to persist knowledge for future queries.",
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Free-form text content of the memory",
                },
                "source_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IDs of evidence events this memory was derived from (optional)",
                },
            },
            "required": ["content"],
        },
    )
    async def create_memory(args: dict[str, Any]) -> dict[str, Any]:
        memory = Memory(
            id=str(uuid7()),
            user_id=user_id,
            content=args["content"],
            source_event_ids=args.get("source_event_ids", []),
        )
        db.insert_memory(memory)
        db.log_memory_op(
            user_id,
            "add",
            input_summary=args["content"][:200],
            output_summary=f"created {memory.id}",
            memory_ids=[memory.id],
        )
        result = {"status": "created", "memory_id": memory.id}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "create_link",
        "Link two memories together with a natural language reason.",
        {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "ID of the first memory",
                },
                "target_id": {
                    "type": "string",
                    "description": "ID of the second memory",
                },
                "reason": {
                    "type": "string",
                    "description": "Why these memories are connected (natural language)",
                },
            },
            "required": ["source_id", "target_id", "reason"],
        },
    )
    async def create_link(args: dict[str, Any]) -> dict[str, Any]:
        link = Link(
            id=str(uuid7()),
            user_id=user_id,
            source_id=args["source_id"],
            target_id=args["target_id"],
            reason=args["reason"],
        )
        db.insert_link(link)
        db.log_memory_op(
            user_id,
            "link",
            input_summary=args["reason"][:200],
            output_summary=f"linked {args['source_id']} <-> {args['target_id']}",
            memory_ids=[args["source_id"], args["target_id"]],
        )
        result = {"status": "linked", "link_id": link.id}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "get_recent_memories",
        "Get the most recently created memories.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": [],
        },
    )
    async def get_recent_memories(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 10), 50)
        results = db.get_recent_memories(user_id, limit=limit)
        formatted = [_format_memory(m) for m in results]
        result = {"count": len(formatted), "memories": formatted}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "get_memex",
        "Read the memex — the agent's accumulated map of this user. Read this first.",
        {},
    )
    async def get_memex(args: dict[str, Any]) -> dict[str, Any]:
        memex = db.get_memex(user_id)
        if memex:
            result = {
                "exists": True,
                "memex": _format_memory(memex),
            }
        else:
            mem_count = db.count_memories(user_id)
            result = {
                "exists": False,
                "memory_count": mem_count,
                "hint": "No memex yet. Use search_memories or search_evidence to explore.",
            }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "browse_timeline",
        "Browse events in a time window. Returns content previews (first 1200 chars). Use 'source' to filter by platform, 'since'/'before' for date range (ISO format), 'limit' for max events (default 50).",
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "ISO date/datetime to filter from (e.g., '2026-02-01')"},
                "before": {"type": "string", "description": "ISO date/datetime to filter until"},
                "source": {"type": "string", "description": "Platform name to filter (e.g., 'github', 'claude-code')"},
                "limit": {"type": "integer", "description": "Max events to return (default 50, max 100)"},
            },
            "required": [],
        },
    )
    async def browse_timeline(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 50), 100)
        events = db.get_events(
            user_id,
            source=args.get("source"),
            since=args.get("since"),
            before=args.get("before"),
            limit=limit,
        )
        formatted = [_format_event(ev) for ev in events]
        result = {"count": len(formatted), "events": formatted}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "cross_reference",
        "Search for a topic across ALL platforms, grouped by source. Discover what patterns exist across the digital footprint.",
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic or keyword to search across all platforms"},
                "limit_per_source": {"type": "integer", "description": "Max events per source (default 10)"},
            },
            "required": ["topic"],
        },
    )
    async def cross_reference(args: dict[str, Any]) -> dict[str, Any]:
        topic = args["topic"]
        limit_per = min(args.get("limit_per_source", 10), 25)
        sources = db.get_sources(user_id)
        # Single query, group results by source in Python
        all_matches = db.search_events(user_id, topic, limit=limit_per * len(sources) if sources else limit_per)
        by_source: dict[str, list] = {}
        for ev in all_matches:
            src = ev["source"]
            if src not in by_source:
                by_source[src] = []
            if len(by_source[src]) < limit_per:
                by_source[src].append(_format_event(ev))
        result = {
            "topic": topic,
            "sources_with_matches": list(by_source.keys()),
            "total_matches": sum(len(v) for v in by_source.values()),
            "by_source": by_source,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    # -----------------------------------------------------------------------
    # Mutation tools (update, supersede, deactivate)
    # -----------------------------------------------------------------------

    @tool(
        "update_memory",
        "Update a memory's content in place. Use for minor edits — fixing details, adding a line, correcting a fact. The memory keeps its ID and history.",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory to update"},
                "new_content": {"type": "string", "description": "The updated content (replaces existing)"},
                "reason": {"type": "string", "description": "Why this update (for audit trail)"},
            },
            "required": ["memory_id", "new_content"],
        },
    )
    async def update_memory(args: dict[str, Any]) -> dict[str, Any]:
        memory_id = args["memory_id"]
        new_content = args["new_content"]
        reason = args.get("reason", "")
        updated = db.update_memory(user_id, memory_id, new_content)
        if not updated:
            result = {"status": "error", "error": f"Memory {memory_id} not found or inactive"}
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        db.log_memory_op(
            user_id,
            "update",
            input_summary=f"{reason[:100]} | {new_content[:100]}" if reason else new_content[:200],
            output_summary=f"updated {memory_id}",
            memory_ids=[memory_id],
        )
        result = {"status": "updated", "memory_id": memory_id}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "supersede_memory",
        "Replace a memory with a new version. The old memory is deactivated and points to the new one. Use when understanding fundamentally changed — not for minor edits.",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory to replace"},
                "new_content": {"type": "string", "description": "Content for the replacement memory"},
                "reason": {"type": "string", "description": "Why this memory is being replaced"},
            },
            "required": ["memory_id", "new_content"],
        },
    )
    async def supersede_memory(args: dict[str, Any]) -> dict[str, Any]:
        old_id = args["memory_id"]
        new_content = args["new_content"]
        reason = args.get("reason", "")
        old_mem = db.get_memory(user_id, old_id)
        if not old_mem or not old_mem.get("active", 0):
            result = {"status": "error", "error": f"Memory {old_id} not found or inactive"}
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        source_ids = old_mem.get("source_event_ids", "[]")
        if isinstance(source_ids, str):
            try:
                source_ids = json.loads(source_ids)
            except (ValueError, TypeError):
                source_ids = []
        new_memory = Memory(
            id=str(uuid7()),
            user_id=user_id,
            content=new_content,
            source_event_ids=source_ids,
        )
        new_id = db.supersede_memory(user_id, old_id, new_memory)
        db.log_memory_op(
            user_id,
            "supersede",
            input_summary=f"{reason[:100]} | replacing {old_id}" if reason else f"replacing {old_id}",
            output_summary=f"superseded {old_id} -> {new_id}",
            memory_ids=[old_id, new_id],
        )
        result = {"status": "superseded", "old_id": old_id, "new_id": new_id}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "deactivate_memory",
        "Retire a memory. It stays in the ledger but becomes invisible to search. Use when a memory is wrong, obsolete, or fully absorbed into another.",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory to deactivate"},
                "reason": {"type": "string", "description": "Why this memory is being retired"},
            },
            "required": ["memory_id"],
        },
    )
    async def deactivate_memory(args: dict[str, Any]) -> dict[str, Any]:
        memory_id = args["memory_id"]
        reason = args.get("reason", "")
        deactivated = db.deactivate_memory(user_id, memory_id)
        if not deactivated:
            result = {"status": "error", "error": f"Memory {memory_id} not found or already inactive"}
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        db.log_memory_op(
            user_id,
            "deactivate",
            input_summary=reason[:200] if reason else f"deactivated {memory_id}",
            output_summary=f"deactivated {memory_id}",
            memory_ids=[memory_id],
        )
        result = {"status": "deactivated", "memory_id": memory_id}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    # -----------------------------------------------------------------------
    # Read tools (inspection / navigation)
    # -----------------------------------------------------------------------

    @tool(
        "get_memory",
        "Read a specific memory by ID. Returns full content + metadata. Use after seeing an ID in search results or links.",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory to read"},
            },
            "required": ["memory_id"],
        },
    )
    async def get_memory(args: dict[str, Any]) -> dict[str, Any]:
        memory_id = args["memory_id"]
        mem = db.get_memory(user_id, memory_id)
        if not mem:
            result = {"status": "not_found", "memory_id": memory_id}
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        result = {
            "status": "found",
            "memory": _format_memory(mem),
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "list_active_memories",
        "List active memories as a compact index: IDs, first line of content, and timestamps. Use to see what exists before diving deeper with get_memory.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 20, max 100)"},
            },
            "required": [],
        },
    )
    async def list_active_memories(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 20), 100)
        memories = db.get_recent_memories(user_id, limit=limit)
        index = []
        for m in memories:
            content = m.get("content", "")
            first_line = content.split("\n")[0][:120] if content else ""
            index.append({
                "id": m["id"],
                "first_line": first_line,
                "created_at": m.get("created_at", ""),
                "updated_at": m.get("updated_at"),
            })
        result = {"count": len(index), "memories": index}
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "get_memory_history",
        "Get the evolution chain of a memory — all versions from oldest to newest. Shows how understanding changed over time.",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of any version in the chain"},
            },
            "required": ["memory_id"],
        },
    )
    async def get_memory_history(args: dict[str, Any]) -> dict[str, Any]:
        memory_id = args["memory_id"]
        chain = db.get_memory_chain(user_id, memory_id)
        if not chain:
            result = {"status": "not_found", "memory_id": memory_id}
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        versions = []
        for m in chain:
            content = m.get("content", "")
            versions.append({
                "id": m["id"],
                "content_preview": content[:300],
                "active": m.get("active", 1),
                "created_at": m.get("created_at", ""),
                "superseded_by": m.get("superseded_by"),
            })
        result = {
            "memory_id": memory_id,
            "versions": len(versions),
            "chain": versions,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
    return [
        search_memories,
        search_evidence,
        follow_links,
        create_memory,
        create_link,
        get_recent_memories,
        get_memex,
        browse_timeline,
        cross_reference,
        update_memory,
        supersede_memory,
        deactivate_memory,
        get_memory,
        list_active_memories,
        get_memory_history,
    ]


def build_memory_mcp_server(db: SykeDB, user_id: str):
    """Create an in-process MCP server with memory tools."""
    tools = create_memory_tools(db, user_id)
    return create_sdk_mcp_server(name="memory", version="1.0.0", tools=tools)
