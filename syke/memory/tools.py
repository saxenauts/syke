"""Memory tools — split into synthesis (write) and ask (read) tool sets."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool
from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.models import Link, Memory
from syke.time import format_for_llm

CONTENT_PREVIEW_LEN = 1200


def _format_memory(mem: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": mem["id"],
        "content": (mem.get("content") or "")[:CONTENT_PREVIEW_LEN],
        "created_at": mem.get("created_at", ""),
        "updated_at": mem.get("updated_at"),
        "active": mem.get("active", 1),
        "source_event_ids": mem.get("source_event_ids", "[]"),
    }
    if result["created_at"]:
        result["local_created_at"] = format_for_llm(result["created_at"])
    if result["updated_at"]:
        result["local_updated_at"] = format_for_llm(result["updated_at"])
    return result


def _format_event(ev: dict[str, Any]) -> dict[str, Any]:
    result = {
        "id": ev["id"],
        "timestamp": ev["timestamp"],
        "source": ev["source"],
        "event_type": ev["event_type"],
        "title": ev.get("title") or "",
        "content_preview": (ev.get("content") or "")[:CONTENT_PREVIEW_LEN],
    }
    if result["timestamp"]:
        result["local_time"] = format_for_llm(result["timestamp"])
    return result


# ===================================================================
# Synthesis tools — write only
# ===================================================================


def create_synthesis_tools(db: SykeDB, user_id: str) -> list[Any]:
    """Build tools for the synthesis agent. Returns [memory_write] only."""

    @tool(
        "memory_write",
        "Unified memory mutation tool. Dispatches to the appropriate operation based on 'op'. "
        "Operations: 'create' (new memory from content), 'update' (edit existing memory in-place), "
        "'supersede' (replace memory with new version, old deactivated), "
        "'deactivate' (retire a memory), 'link' (connect two memories with a reason).",
        {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["create", "update", "supersede", "deactivate", "link"],
                    "description": "The operation to perform",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Parameters for the operation. "
                        "create: {content: str, source_event_ids?: str[]}. "
                        "update: {memory_id: str, new_content: str, reason?: str}. "
                        "supersede: {memory_id: str, new_content: str, reason?: str}. "
                        "deactivate: {memory_id: str, reason?: str}. "
                        "link: {source_id: str, target_id: str, reason: str}."
                    ),
                },
            },
            "required": ["op", "params"],
        },
    )
    async def memory_write(args: dict[str, Any]) -> dict[str, Any]:
        op = args.get("op", "")
        params = args.get("params", {})

        if op == "create":
            content = params.get("content")
            if not content:
                return _err("create requires 'content' parameter")
            memory = Memory(
                id=str(uuid7()),
                user_id=user_id,
                content=content,
                source_event_ids=params.get("source_event_ids", []),
            )
            db.insert_memory(memory)
            db.log_memory_op(
                user_id, "add",
                input_summary=content[:200],
                output_summary=f"created {memory.id}",
                memory_ids=[memory.id],
            )
            return _ok({"status": "created", "memory_id": memory.id})

        elif op == "update":
            memory_id = params.get("memory_id")
            new_content = params.get("new_content")
            if not memory_id or not new_content:
                return _err("update requires 'memory_id' and 'new_content'")
            reason = params.get("reason", "")
            updated = db.update_memory(user_id, memory_id, new_content)
            if not updated:
                return _err(f"Memory {memory_id} not found or inactive")
            db.log_memory_op(
                user_id, "update",
                input_summary=f"{reason[:100]} | {new_content[:100]}" if reason else new_content[:200],
                output_summary=f"updated {memory_id}",
                memory_ids=[memory_id],
            )
            return _ok({"status": "updated", "memory_id": memory_id})

        elif op == "supersede":
            memory_id = params.get("memory_id")
            new_content = params.get("new_content")
            if not memory_id or not new_content:
                return _err("supersede requires 'memory_id' and 'new_content'")
            reason = params.get("reason", "")
            old_mem = db.get_memory(user_id, memory_id)
            if not old_mem or not old_mem.get("active", 0):
                return _err(f"Memory {memory_id} not found or inactive")
            source_ids = old_mem.get("source_event_ids", "[]")
            if isinstance(source_ids, str):
                try:
                    source_ids = json.loads(source_ids)
                except (ValueError, TypeError):
                    source_ids = []
            new_memory = Memory(
                id=str(uuid7()), user_id=user_id, content=new_content, source_event_ids=source_ids
            )
            new_id = db.supersede_memory(user_id, memory_id, new_memory)
            db.log_memory_op(
                user_id, "supersede",
                input_summary=f"{reason[:100]} | replacing {memory_id}" if reason else f"replacing {memory_id}",
                output_summary=f"superseded {memory_id} -> {new_id}",
                memory_ids=[memory_id, new_id],
            )
            return _ok({"status": "superseded", "old_id": memory_id, "new_id": new_id})

        elif op == "deactivate":
            memory_id = params.get("memory_id")
            if not memory_id:
                return _err("deactivate requires 'memory_id'")
            reason = params.get("reason", "")
            deactivated = db.deactivate_memory(user_id, memory_id)
            if not deactivated:
                return _err(f"Memory {memory_id} not found or already inactive")
            db.log_memory_op(
                user_id, "deactivate",
                input_summary=reason[:200] if reason else f"deactivated {memory_id}",
                output_summary=f"deactivated {memory_id}",
                memory_ids=[memory_id],
            )
            return _ok({"status": "deactivated", "memory_id": memory_id})

        elif op == "link":
            source_id = params.get("source_id")
            target_id = params.get("target_id")
            reason = params.get("reason")
            if not source_id or not target_id or not reason:
                return _err("link requires 'source_id', 'target_id', and 'reason'")
            link = Link(
                id=str(uuid7()), user_id=user_id,
                source_id=source_id, target_id=target_id, reason=reason,
            )
            db.insert_link(link)
            db.log_memory_op(
                user_id, "link",
                input_summary=reason[:200],
                output_summary=f"linked {source_id} <-> {target_id}",
                memory_ids=[source_id, target_id],
            )
            return _ok({"status": "linked", "link_id": link.id})

        else:
            return _err(f"Invalid operation '{op}'. Valid ops: create, update, supersede, deactivate, link")

    return [memory_write]


# ===================================================================
# Ask tools — read only
# ===================================================================


def create_ask_tools(db: SykeDB, user_id: str) -> list[Any]:
    """Build tools for the ask agent. Returns 9 read-only tools."""

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
        return _ok({"query": args["query"], "count": len(formatted), "memories": formatted})

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
        return _ok({"query": args["query"], "count": len(formatted), "events": formatted})

    @tool(
        "follow_links",
        "Get all memories linked to a given memory, with the reason for each link.",
        {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory to follow links from"},
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
        return _ok({"memory_id": memory_id, "count": len(formatted), "linked_memories": formatted})

    @tool(
        "get_overview",
        "Read the overview document — the starting point for navigating this user's knowledge.",
        {},
    )
    async def get_overview(args: dict[str, Any]) -> dict[str, Any]:
        memex = db.get_memex(user_id)
        if memex:
            return _ok({"exists": True, "overview": _format_memory(memex)})
        else:
            mem_count = db.count_memories(user_id)
            return _ok({"exists": False, "memory_count": mem_count, "hint": "No overview yet. Use search_memories or search_evidence to explore."})

    @tool(
        "browse_timeline",
        "Browse events in a time window. Use 'source' to filter by platform, 'since'/'before' for date range, 'limit' for max events.",
        {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "ISO date/datetime to filter from"},
                "before": {"type": "string", "description": "ISO date/datetime to filter until"},
                "source": {"type": "string", "description": "Platform name to filter"},
                "limit": {"type": "integer", "description": "Max events (default 50, max 100)"},
            },
            "required": [],
        },
    )
    async def browse_timeline(args: dict[str, Any]) -> dict[str, Any]:
        limit = min(args.get("limit", 50), 100)
        events = db.get_events(
            user_id, source=args.get("source"),
            since=args.get("since"), before=args.get("before"), limit=limit,
        )
        formatted = [_format_event(ev) for ev in events]
        return _ok({"count": len(formatted), "events": formatted})

    @tool(
        "cross_reference",
        "Search for a topic across ALL platforms, grouped by source.",
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic or keyword to search"},
                "limit_per_source": {"type": "integer", "description": "Max events per source (default 10)"},
            },
            "required": ["topic"],
        },
    )
    async def cross_reference(args: dict[str, Any]) -> dict[str, Any]:
        topic = args["topic"]
        limit_per = min(args.get("limit_per_source", 10), 25)
        sources = db.get_sources(user_id)
        all_matches = db.search_events(
            user_id, topic, limit=limit_per * len(sources) if sources else limit_per
        )
        by_source: dict[str, list[dict[str, Any]]] = {}
        for ev in all_matches:
            src = ev["source"]
            if src not in by_source:
                by_source[src] = []
            if len(by_source[src]) < limit_per:
                by_source[src].append(_format_event(ev))
        return _ok({
            "topic": topic,
            "sources_with_matches": list(by_source.keys()),
            "total_matches": sum(len(v) for v in by_source.values()),
            "by_source": by_source,
        })

    @tool(
        "get_memory",
        "Read a specific memory by ID. Returns full content + metadata.",
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
            return _ok({"status": "not_found", "memory_id": memory_id})
        return _ok({"status": "found", "memory": _format_memory(mem)})

    @tool(
        "list_active_memories",
        "List active memories as a compact index: IDs, first line, timestamps.",
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
        return _ok({"count": len(index), "memories": index})

    @tool(
        "get_memory_history",
        "Get the evolution chain of a memory — all versions from oldest to newest.",
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
            return _ok({"status": "not_found", "memory_id": memory_id})
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
        return _ok({"memory_id": memory_id, "versions": len(versions), "chain": versions})

    return [
        search_memories,
        search_evidence,
        follow_links,
        get_overview,
        browse_timeline,
        cross_reference,
        get_memory,
        list_active_memories,
        get_memory_history,
    ]


# ===================================================================
# Helpers
# ===================================================================


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps({"status": "error", "error": msg})}]}
