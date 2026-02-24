"""Ask agent — answers questions about a user by exploring their timeline."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    ClaudeSDKError,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    PermissionResultAllow,
    create_sdk_mcp_server,
)

log = logging.getLogger(__name__)

from syke.config import ASK_MODEL, ASK_MAX_TURNS, ASK_BUDGET
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


def _patch_sdk_for_rate_limit() -> None:
    """Patch message_parser to tolerate rate_limit_event advisory (CLI 2.1.45+).

    rate_limit_event is an informational quota-status message, not an error.
    SDK 0.1.38 raises MessageParseError for it — this makes it return a
    SystemMessage instead so the stream continues to the actual response.
    """
    try:
        import claude_agent_sdk._internal.message_parser as _mp

        if getattr(_mp.parse_message, "_rate_limit_patched", False):
            return
        _orig = _mp.parse_message

        def _patched(data: dict) -> object:
            if data.get("type") == "rate_limit_event":
                log.debug("Skipping rate_limit_event advisory (CLI 2.1.45+)")
                return _mp.SystemMessage(subtype="rate_limit_event", data=data)
            return _orig(data)

        _patched._rate_limit_patched = True
        _mp.parse_message = _patched
    except Exception:
        pass  # best-effort; never break ask()


_patch_sdk_for_rate_limit()


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


async def _run_ask(db: SykeDB, user_id: str, question: str) -> str:
    event_count = db.count_events(user_id)
    profile = db.get_latest_profile(user_id)
    if event_count == 0 and not profile:
        return "No data yet. Run `syke setup` to collect your digital footprint first."

    try:
        os.environ.pop("CLAUDECODE", None)

        env_patch: dict[str, str] = {}
        if (Path.home() / ".claude").is_dir():
            env_patch["ANTHROPIC_API_KEY"] = ""

        # Build MCP server from memory tools only
        memory_tools = create_memory_tools(db, user_id)
        server = create_sdk_mcp_server(name="syke", version="1.0.0", tools=memory_tools)

        memex_content = get_memex_for_injection(db, user_id)
        system_prompt = ASK_SYSTEM_PROMPT_TEMPLATE.format(memex_content=memex_content)

        allowed = [f"mcp__syke__{name}" for name in ASK_TOOLS]

        async def _allow_all(tool_name, tool_input, context=None):
            return PermissionResultAllow()

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"syke": server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=ASK_MAX_TURNS,
            max_budget_usd=ASK_BUDGET,
            model=ASK_MODEL,
            can_use_tool=_allow_all,
            env=env_patch,
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

        return answer_parts[-1] if answer_parts else ""
    except Exception as e:
        return (
            f"ask() failed: {e}\n"
            "Fix: ensure you are logged into Claude Code ('claude /login'). "
            "API key fallback: set ANTHROPIC_API_KEY in environment."
        )


def ask(db: SykeDB, user_id: str, question: str) -> str:
    """Synchronous entry point."""
    try:
        return asyncio.run(_run_ask(db, user_id, question))
    except Exception as e:
        return f"Error: {e}"
