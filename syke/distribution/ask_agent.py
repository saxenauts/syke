"""Ask agent — answers questions about a user by exploring their timeline."""

from __future__ import annotations

import asyncio
import logging
import os

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    ClaudeSDKError,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
)

log = logging.getLogger(__name__)

from syke.config import (
    ASK_MODEL,
    ASK_MAX_TURNS,
    ASK_BUDGET,
)
from syke.db import SykeDB
from syke.memory.tools import create_memory_tools
from syke.memory.memex import get_memex_for_injection

ASK_TOOLS = [
    "search_memories",
    "search_evidence",
    "follow_links",
    "get_memex",
    "browse_timeline",
    "cross_reference",
    "get_memory",
    "list_active_memories",
    "get_memory_history",
]

ASK_SYSTEM_PROMPT_TEMPLATE = """You are Syke, a personal memory agent. You know a user's digital footprint — conversations, code, emails, activity across platforms.

Answer the question from an AI assistant working with this user.

## Your Memory
{memex_content}

## Strategy (follow this order)
1. Read the memex above — it's your map of this user. Stable things, active things, context. If it answers the question, respond immediately.
2. Search memories (search_memories) for extracted knowledge. These are persistent insights.
3. If memories don't have the answer, search raw evidence (search_evidence) for specific facts.
4. Follow links (follow_links) to discover connected memories.
5. For cross-platform connections: cross_reference.
6. For recent activity: browse_timeline with date filters.
7. If you discover something worth remembering, create a memory (create_memory) for future queries.

## Rules
- Be PRECISE. Real names, dates, project names.
- Be CONCISE. 1-5 sentences max.
- If you don't have enough data, say so honestly.
- Prefer memories over raw evidence — memories are distilled knowledge.
- Create memories when you discover facts that future queries would benefit from.
- Link related memories when you notice connections."""


def _log_ask_metrics(user_id: str, result: ResultMessage, model: str) -> None:
    """Log ask() cost/usage to metrics.jsonl. Silent on failure."""
    try:
        from syke.metrics import MetricsTracker

        tracker = MetricsTracker(user_id)
        with tracker.track("ask", model=model) as m:
            m.cost_usd = result.total_cost_usd or 0.0
            m.num_turns = result.num_turns or 0
            m.duration_api_ms = result.duration_api_ms or 0
            usage = getattr(result, "usage", {}) or {}
            m.input_tokens = usage.get("input_tokens", 0)
            m.output_tokens = usage.get("output_tokens", 0)
    except Exception:
        pass  # metrics failure must never break ask()

def _local_fallback(db: SykeDB, user_id: str, question: str) -> str:
    """Fallback when Agent SDK fails: return best local data from DB."""
    parts: list[str] = []

    # 1. Include the memex if available
    memex = get_memex_for_injection(db, user_id)
    if memex:
        parts.append(memex)

    # 2. Search memories for the question keywords
    try:
        keywords = [w for w in question.lower().split() if len(w) > 3][:5]
        if keywords:
            query = " ".join(keywords)
            rows = db.conn.execute(
                """SELECT title, content FROM memories
                   WHERE user_id = ? AND status = 'active'
                   AND (title LIKE ? OR content LIKE ?)
                   ORDER BY updated_at DESC LIMIT 5""",
                (user_id, f"%{query}%", f"%{query}%"),
            ).fetchall()
            if rows:
                parts.append("\n--- Relevant memories ---")
                for title, content in rows:
                    parts.append(f"**{title}**: {content[:300]}")
    except Exception:
        pass  # fallback must not fail

    if parts:
        return "\n\n".join(parts) + "\n\n[local fallback — ask agent unavailable]"
    return "No answer available. The ask agent could not be reached and no local data matched your query."


async def _run_ask(db: SykeDB, user_id: str, question: str) -> str:
    event_count = db.count_events(user_id)
    profile = db.get_latest_profile(user_id)
    if event_count == 0 and not profile:
        return "No data yet. Run `syke setup` to collect your digital footprint first."

    try:
        os.environ.pop("CLAUDECODE", None)

        # Build MCP server from memory tools only
        memory_tools = create_memory_tools(db, user_id)
        server = create_sdk_mcp_server(name="syke", version="1.0.0", tools=memory_tools)

        memex_content = get_memex_for_injection(db, user_id)
        system_prompt = ASK_SYSTEM_PROMPT_TEMPLATE.format(memex_content=memex_content)

        allowed = [f"mcp__syke__{name}" for name in ASK_TOOLS]

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"syke": server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=ASK_MAX_TURNS,
            max_budget_usd=ASK_BUDGET,
            model=ASK_MODEL,
            env={},
        )

        task = f"Answer this question about user '{user_id}' ({event_count} events in timeline):\n\n{question}"
        answer_parts: list[str] = []

        async with ClaudeSDKClient(options=options) as client:
            await client.query(task)
            try:
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                answer_parts.append(block.text.strip())
                    elif isinstance(message, ResultMessage):
                        _log_ask_metrics(
                            user_id=user_id,
                            result=message,
                            model=ASK_MODEL or "default",
                        )
                        break
            except ClaudeSDKError as stream_err:
                if "Unknown message type" not in str(stream_err):
                    raise
                log.warning("ask() stream interrupted by unknown event: %s", stream_err)

        if answer_parts:
            return answer_parts[-1]

        # Agent returned nothing — fall back to local DB
        log.warning("ask() returned empty for user %s, question: %s", user_id, question[:80])
        return _local_fallback(db, user_id, question)
    except ClaudeSDKError as sdk_err:
        log.error("ask() SDK error for %s: %s", user_id, sdk_err)
        return _local_fallback(db, user_id, question)
    except Exception as e:
        log.error("ask() failed for %s: %s", user_id, e)
        return _local_fallback(db, user_id, question)


def ask(db: SykeDB, user_id: str, question: str) -> str:
    """Synchronous entry point."""
    try:
        return asyncio.run(_run_ask(db, user_id, question))
    except Exception as e:
        log.error("ask() sync wrapper failed: %s", e)
        return _local_fallback(db, user_id, question)
