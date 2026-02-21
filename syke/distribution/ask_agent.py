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
)

log = logging.getLogger(__name__)

from syke.config import ASK_MODEL, ASK_MAX_TURNS, ASK_BUDGET
from syke.db import SykeDB
from syke.perception.tools import build_perception_mcp_server

ASK_TOOLS = [
    "get_source_overview",
    "browse_timeline",
    "search_footprint",
    "cross_reference",
    "read_previous_profile",
]

ASK_SYSTEM_PROMPT = """You are Syke, a personal memory agent. You know a user's digital footprint — conversations, code, emails, activity across platforms.

Answer the question from an AI assistant working with this user.

## Strategy (follow this order)
1. ALWAYS start with read_previous_profile. The synthesized identity often answers the question directly — check before searching.
2. If the profile answers it, respond immediately. Do NOT search when the profile suffices.
3. For specific facts not in the profile: search_footprint with SINGLE keywords (not phrases).
4. For cross-platform connections: cross_reference.
5. For recent activity: browse_timeline with date filters.

## Rules
- Be PRECISE. Real names, dates, project names.
- Be CONCISE. 1-5 sentences max.
- If you don't have enough data, say so honestly.
- 2-3 tool calls max. Profile first, search only if needed.
- Search uses keyword matching. Single words only.
- Answer from the profile in turn 1 if possible."""

TOOL_PREFIX = "mcp__perception__"


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


ASK_TIMEOUT_S = 25


async def _run_agent(db: SykeDB, user_id: str, question: str) -> str:
    """Inner agent execution — separated so _run_ask can wrap with timeout."""
    event_count = db.count_events(user_id)

    os.environ.pop("CLAUDECODE", None)

    env_patch: dict[str, str] = {}
    if (Path.home() / ".claude").is_dir():
        env_patch["ANTHROPIC_API_KEY"] = ""

    perception_server = build_perception_mcp_server(db, user_id)
    allowed = [f"{TOOL_PREFIX}{name}" for name in ASK_TOOLS]

    async def _allow_all(tool_name, tool_input, context=None):
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        system_prompt=ASK_SYSTEM_PROMPT,
        mcp_servers={"perception": perception_server},
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
                    _log_ask_metrics(user_id=user_id, result=message, model=ASK_MODEL or "default")
                    break
        except ClaudeSDKError as stream_err:
            if "Unknown message type" not in str(stream_err):
                raise
            log.warning("ask() stream interrupted by unknown event: %s", stream_err)

    return answer_parts[-1] if answer_parts else ""


async def _run_ask(db: SykeDB, user_id: str, question: str) -> str:
    event_count = db.count_events(user_id)
    profile = db.get_latest_profile(user_id)
    if event_count == 0 and not profile:
        return "No data yet. Run `syke setup` to collect your digital footprint first."

    try:
        return await asyncio.wait_for(
            _run_agent(db, user_id, question),
            timeout=ASK_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("ask() timed out after %ds for question: %s", ASK_TIMEOUT_S, question[:80])
        if profile:
            from syke.distribution.formatters import format_profile
            return (
                f"ask() timed out after {ASK_TIMEOUT_S}s. "
                f"Here's the current profile summary:\n\n"
                + format_profile(profile, "markdown")
            )
        return f"ask() timed out after {ASK_TIMEOUT_S}s. Try a more specific question."
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
