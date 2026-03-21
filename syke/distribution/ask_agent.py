"""Ask agent — answers questions about a user by exploring their timeline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk.types import StreamEvent

from syke.config import (
    ASK_BUDGET,
    ASK_MAX_TURNS,
    ASK_MODEL,
    ASK_TIMEOUT,
    clean_claude_env,
)
from syke.db import SykeDB
from syke.llm import build_agent_env
from syke.memory.memex import get_memex_for_injection
from syke.memory.tools import create_memory_tools
from syke.time import temporal_grounding_block

log = logging.getLogger(__name__)


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

{temporal_context}

## Strategy (follow this order)
1. Read the memex above — it's your map of this user. Stable things, active things, context. If it answers the question, respond immediately.
2. Search memories (search_memories) for extracted knowledge. These are persistent insights.
3. If memories don't have the answer, search raw evidence (search_evidence) for specific facts.
4. Follow links (follow_links) to discover connected memories.
5. For cross-platform connections: cross_reference.
6. For recent activity: browse_timeline with date filters. Use the timestamps in Temporal Context above to construct 'since' and 'before' parameters in ISO format.
7. If you discover something worth remembering, create a memory (create_memory) for future queries.

## Rules
- Be PRECISE. Real names, dates, project names.
- Be CONCISE. Answer in 1-5 sentences for simple questions, longer for broad ones — but always synthesize, never dump raw data.
- CONVERGE QUICKLY. Use 1-3 tool calls for most questions. For broad questions, use up to 5 then STOP and synthesize what you have. Never chase completeness — answer with what you found.
- If you don't have enough data, say so honestly.
- Prefer memories over raw evidence — memories are distilled knowledge.
- Create memories when you discover facts that future queries would benefit from.
- Link related memories when you notice connections.
- ALWAYS deliver a final answer. Never end with "let me search for more" — synthesize and respond."""


def _log_ask_metrics(
    user_id: str, result: ResultMessage, model: str, wall_seconds: float = 0.0
) -> dict[str, float]:
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
        summary = {
            "cost_usd": cost,
            "duration_seconds": secs,
            "tokens": in_tok + out_tok,
        }
    except Exception:
        pass  # metrics failure must never break ask()
    return summary


class AskError(RuntimeError):
    """Raised when the ask agent fails."""


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
    if on_event is None:
        return
    try:
        on_event(event)
    except BrokenPipeError:
        raise
    except Exception:
        pass


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
    if event_count == 0:
        return (
            "No data yet. Run `syke setup` to collect your digital footprint first.",
            {},
        )

    import time as _time

    streaming = on_event is not None

    with clean_claude_env():
        memory_tools = create_memory_tools(db, user_id)
        server = create_sdk_mcp_server(name="syke", version="1.0.0", tools=memory_tools)

        memex_content = get_memex_for_injection(db, user_id)
        tg = temporal_grounding_block()
        system_prompt = ASK_SYSTEM_PROMPT_TEMPLATE.format(
            memex_content=memex_content,
            temporal_context=tg,
        )

        allowed = [f"mcp__syke__{name}" for name in ASK_TOOLS]

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"syke": server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=ASK_MAX_TURNS,
            max_budget_usd=ASK_BUDGET,
            model=ASK_MODEL,
            include_partial_messages=streaming,
            thinking={"type": "enabled", "budget_tokens": 10000},
            env=build_agent_env(),
        )

        task = f"Answer this question about user '{user_id}' ({event_count} events in timeline):\n\n{question}"
        result_message: ResultMessage | None = None
        answer_parts: list[str] = []
        cost_summary: dict[str, float] = {}
        wall_start = _time.monotonic()

        async with ClaudeSDKClient(options=options) as client:
            await client.query(task)
            try:
                async for message in client.receive_response():
                    if isinstance(message, StreamEvent) and on_event:
                        ev = message.event
                        if ev.get("type") == "content_block_delta":
                            delta = ev.get("delta", {})
                            dt = delta.get("type", "")
                            if dt == "thinking_delta":
                                thinking = delta.get("thinking", "")
                                if thinking:
                                    _emit(on_event, AskEvent("thinking", thinking))

                    elif isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text.strip():
                                answer_parts.append(block.text.strip())
                            elif isinstance(block, ToolUseBlock):
                                _emit(
                                    on_event,
                                    AskEvent(
                                        "tool_call",
                                        block.name,
                                        {"input": block.input},
                                    ),
                                )

                    elif isinstance(message, ResultMessage):
                        result_message = message
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

        if result_message and result_message.result:
            return result_message.result, cost_summary

        if answer_parts:
            return "\n\n".join(answer_parts), cost_summary

        raise AskError("Agent returned no text response")


# ---------------------------------------------------------------------------
# Public sync entry points
# ---------------------------------------------------------------------------


async def _run_ask_with_timeout(
    db: SykeDB,
    user_id: str,
    question: str,
    on_event: Callable[[AskEvent], None] | None = None,
) -> tuple[str, dict[str, float]]:
    try:
        return await asyncio.wait_for(
            _run_ask(db, user_id, question, on_event=on_event),
            timeout=ASK_TIMEOUT,
        )
    except TimeoutError as e:
        raise AskError(f"Timed out after {ASK_TIMEOUT}s") from e


def ask(db: SykeDB, user_id: str, question: str) -> tuple[str, dict[str, float]]:
    return asyncio.run(_run_ask_with_timeout(db, user_id, question))


def ask_stream(
    db: SykeDB,
    user_id: str,
    question: str,
    on_event: Callable[[AskEvent], None],
) -> tuple[str, dict[str, float]]:
    return asyncio.run(_run_ask_with_timeout(db, user_id, question, on_event=on_event))
