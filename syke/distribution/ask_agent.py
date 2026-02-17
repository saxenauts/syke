"""Ask agent — answers questions about a user by exploring their timeline."""

from __future__ import annotations

import asyncio

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    PermissionResultAllow,
)

from syke.db import SykeDB
from syke.perception.tools import build_perception_mcp_server

ASK_TOOLS = [
    "get_source_overview",
    "browse_timeline",
    "search_footprint",
    "cross_reference",
    "read_previous_profile",
]

ASK_SYSTEM_PROMPT = """You are Syke, a personal context agent. You have deep knowledge of a user's digital footprint — conversations, code, emails, activity across platforms.

Answer the question from an AI assistant working with this user.

## How to Work
1. Start with read_previous_profile — the synthesized identity + world state. Often this alone answers the question.
2. For specifics: search_footprint with SINGLE keywords (not phrases).
3. For cross-platform: cross_reference.
4. For recent activity: browse_timeline with date filters.

## Rules
- Be PRECISE. Real names, dates, project names.
- Be CONCISE. 1-5 sentences max.
- If you don't have enough data, say so.
- 2-4 tool calls max.
- Search uses keyword matching. Single words only."""

TOOL_PREFIX = "mcp__perception__"


async def _run_ask(db: SykeDB, user_id: str, question: str) -> str:
    event_count = db.count_events(user_id)
    profile = db.get_latest_profile(user_id)
    if event_count == 0 and not profile:
        return "No data yet. Run `syke setup` to collect your digital footprint first."

    from syke.config import load_api_key
    api_key = load_api_key()
    if not api_key:
        return "ask() requires ANTHROPIC_API_KEY to be set. The timeline tools (query_timeline, search_events, get_event) work without it."
    import os
    os.environ["ANTHROPIC_API_KEY"] = api_key

    perception_server = build_perception_mcp_server(db, user_id)
    allowed = [f"{TOOL_PREFIX}{name}" for name in ASK_TOOLS]

    async def _allow_all(tool_name, tool_input, context=None):
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        system_prompt=ASK_SYSTEM_PROMPT,
        mcp_servers={"perception": perception_server},
        allowed_tools=allowed,
        permission_mode="bypassPermissions",
        max_turns=8,
        max_budget_usd=0.15,
        model="sonnet",
        can_use_tool=_allow_all,
    )

    task = f"Answer this question about user '{user_id}' ({event_count} events in timeline):\n\n{question}"
    answer_parts: list[str] = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(task)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        answer_parts.append(block.text.strip())
            elif isinstance(message, ResultMessage):
                break

    return answer_parts[-1] if answer_parts else "Could not answer. Try rephrasing."


def ask(db: SykeDB, user_id: str, question: str) -> str:
    """Synchronous entry point."""
    try:
        return asyncio.run(asyncio.wait_for(_run_ask(db, user_id, question), timeout=60.0))
    except asyncio.TimeoutError:
        return "Question timed out. Try a more specific question."
    except Exception as e:
        return f"Error: {e}"
