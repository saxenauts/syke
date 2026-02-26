"""Ask agent — answers questions about a user by exploring their timeline."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    ClaudeSDKError,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    )
from claude_agent_sdk.types import StreamEvent
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


def _log_ask_metrics(user_id: str, result: ResultMessage, model: str, wall_seconds: float = 0.0) -> dict[str, float]:
    """Log ask() cost/usage to metrics.jsonl. Returns cost summary. Silent on failure."""
    summary: dict[str, float] = {}
    try:
        from syke.metrics import MetricsTracker, RunMetrics

        tracker = MetricsTracker(user_id)
        api_ms = result.duration_api_ms or 0
        usage = getattr(result, "usage", {}) or {}
        cost = result.total_cost_usd or 0.0
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        secs = wall_seconds if wall_seconds > 0 else api_ms / 1000.0
        metrics = RunMetrics(
            operation="ask",
            user_id=user_id,
            cost_usd=cost,
            num_turns=result.num_turns or 0,
            duration_api_ms=api_ms,
            duration_seconds=secs,
            input_tokens=in_tok,
            output_tokens=out_tok,
            details={"model": model},
        )
        tracker.record(metrics)
        summary = {"cost_usd": cost, "duration_seconds": secs, "tokens": in_tok + out_tok}
    except Exception:
        pass  # metrics failure must never break ask()
    return summary

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


# ---------------------------------------------------------------------------
# Streaming event type
# ---------------------------------------------------------------------------


@dataclass
class AskEvent:
    """Event emitted during ask streaming."""

    type: Literal["thinking", "text", "tool_call"]
    content: str
    metadata: dict[str, Any] | None = None


def _emit(on_event: Callable[[AskEvent], None] | None, event: AskEvent) -> None:
    """Safely emit an event to the callback."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        pass  # UI callback failure must never break ask()


# ---------------------------------------------------------------------------
# Core async implementation
# ---------------------------------------------------------------------------


async def _run_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    on_event: Callable[[AskEvent], None] | None = None,
) -> tuple[str, dict[str, float]]:
    event_count = db.count_events(user_id)
    profile = db.get_latest_profile(user_id)
    if event_count == 0 and not profile:
        return "No data yet. Run `syke setup` to collect your digital footprint first.", {}

    import time as _time

    streaming = on_event is not None

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
            include_partial_messages=streaming,
            thinking={"type": "enabled", "budget_tokens": 10000},
        )

        task = f"Answer this question about user '{user_id}' ({event_count} events in timeline):\n\n{question}"
        answer_parts: list[str] = []
        cost_summary: dict[str, float] = {}
        wall_start = _time.monotonic()

        async with ClaudeSDKClient(options=options) as client:
            await client.query(task)
            try:
                async for message in client.receive_response():
                    # -- Partial deltas (only when streaming) --
                    if isinstance(message, StreamEvent) and on_event:
                        ev = message.event
                        if ev.get("type") == "content_block_delta":
                            delta = ev.get("delta", {})
                            dt = delta.get("type", "")
                            if dt == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    _emit(on_event, AskEvent("text", text))
                            elif dt == "thinking_delta":
                                thinking = delta.get("thinking", "")
                                if thinking:
                                    _emit(on_event, AskEvent("thinking", thinking))

                    # -- Complete messages --
                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                answer_parts.append(block.text.strip())
                            elif isinstance(block, ToolUseBlock):
                                _emit(on_event, AskEvent(
                                    "tool_call", block.name, {"input": block.input},
                                ))
                            # ThinkingBlock: already streamed via deltas when
                            # streaming, and not shown otherwise.

                    elif isinstance(message, ResultMessage):
                        wall_seconds = _time.monotonic() - wall_start
                        cost_summary = _log_ask_metrics(
                            user_id=user_id,
                            result=message,
                            model=ASK_MODEL or "default",
                            wall_seconds=wall_seconds,
                        )
                        break
            except ClaudeSDKError as stream_err:
                if "Unknown message type" not in str(stream_err):
                    raise
                log.warning("ask() stream interrupted by unknown event: %s", stream_err)

        if answer_parts:
            # Return all text blocks joined — the agent may answer across
            # multiple turns (search → answer → memory-save confirmation).
            return "\n\n".join(answer_parts), cost_summary

        # Agent returned nothing — fall back to local DB
        log.warning("ask() returned empty for user %s, question: %s", user_id, question[:80])
        return _local_fallback(db, user_id, question), cost_summary
    except ClaudeSDKError as sdk_err:
        log.error("ask() SDK error for %s: %s", user_id, sdk_err)
        return _local_fallback(db, user_id, question), {}
    except Exception as e:
        log.error("ask() failed for %s: %s", user_id, e)
        return _local_fallback(db, user_id, question), {}

# ---------------------------------------------------------------------------
# Public sync entry points
# ---------------------------------------------------------------------------


def ask(db: SykeDB, user_id: str, question: str) -> tuple[str, dict[str, float]]:
    """Non-streaming entry point. Returns (answer, cost_summary)."""
    try:
        return asyncio.run(_run_ask(db, user_id, question))
    except Exception as e:
        log.error("ask() sync wrapper failed: %s", e)
        return _local_fallback(db, user_id, question), {}


def ask_stream(
    db: SykeDB,
    user_id: str,
    question: str,
    on_event: Callable[[AskEvent], None],
) -> tuple[str, dict[str, float]]:
    """Streaming entry point. Calls on_event(AskEvent) for each event.

    Returns (answer, cost_summary) after the stream completes.
    """
    try:
        return asyncio.run(_run_ask(db, user_id, question, on_event=on_event))
    except Exception as e:
        log.error("ask_stream() failed: %s", e)
        return _local_fallback(db, user_id, question), {}
