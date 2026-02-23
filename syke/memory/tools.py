"""Memory tools for the ask() agent — search, navigate, create, link."""

from __future__ import annotations

import json
import time
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
        start = time.monotonic()
        limit = min(args.get("limit", 15), 50)
        results = db.search_memories(user_id, args["query"], limit=limit)
        formatted = [_format_memory(m) for m in results]
        elapsed_ms = int((time.monotonic() - start) * 1000)
        db.log_memory_op(
            user_id,
            "retrieve",
            input_summary=args["query"],
            output_summary=f"{len(formatted)} results",
            memory_ids=[m["id"] for m in formatted],
            duration_ms=elapsed_ms,
        )
        result = {
            "query": args["query"],
            "count": len(formatted),
            "memories": formatted,
        }
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

    @tool(
        "search_evidence",
        "BM25 full-text search over raw events (evidence ledger). Use when memories don't have the answer.",
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
        start = time.monotonic()
        limit = min(args.get("limit", 15), 50)
        results = db.search_events_fts(user_id, args["query"], limit=limit)
        formatted = [_format_event(ev) for ev in results]
        elapsed_ms = int((time.monotonic() - start) * 1000)
        db.log_memory_op(
            user_id,
            "retrieve",
            input_summary=f"evidence:{args['query']}",
            output_summary=f"{len(formatted)} events",
            duration_ms=elapsed_ms,
        )
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
        "Read the memex (world index). This is the routing table — active stories, key entities, shortcuts. Read this first.",
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

    return [
        search_memories,
        search_evidence,
        follow_links,
        create_memory,
        create_link,
        get_recent_memories,
        get_memex,
    ]


def build_memory_mcp_server(db: SykeDB, user_id: str):
    """Create an in-process MCP server with memory tools."""
    tools = create_memory_tools(db, user_id)
    return create_sdk_mcp_server(name="memory", version="1.0.0", tools=tools)
