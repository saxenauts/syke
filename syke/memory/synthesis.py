"""Synthesis — extracts memories from new events (Mastra Observer pattern).

Runs after ingestion. Reads new events + memex + recent memories,
uses an agent to extract persistent knowledge, then updates the memex.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    PermissionResultAllow,
)

from syke.config import SYNC_MODEL, SYNC_MAX_TURNS, SYNC_BUDGET
from syke.db import SykeDB
from syke.memory.memex import (
    get_memex_for_injection,
    update_memex,
    bootstrap_memex_from_profile,
)
from syke.memory.tools import build_memory_mcp_server, MEMORY_TOOL_NAMES

log = logging.getLogger(__name__)

SYNTHESIS_THRESHOLD = 5
MEMORY_PREFIX = "mcp__memory__"

SYNTHESIS_PROMPT = """You are Syke's memory synthesizer. You maintain a living map of who this person is — their projects, relationships, decisions, preferences, and stories.

## Current Memex (The Map)
{memex_content}
{new_events_summary}
## Instructions

STEP 1 — ORIENT:
Read the memex above. It's your map — landmarks (stable entities), active trails (what's hot), and the world state. Understand what already exists before creating anything new.

STEP 2 — EXTRACT & EVOLVE MEMORIES:
For each new event, decide:

a) **New knowledge?** → create_memory. Write it as prose — a story, not a fact list.
   Link it to related memories with create_link.

b) **Updates existing knowledge?** → First check: does a memory about this already exist?
   - Minor update (new detail, correction) → update_memory with the existing memory_id.
   - Major shift (understanding changed fundamentally) → supersede_memory to create a new version.

c) **Makes something obsolete?** → deactivate_memory. The old memory stays in the ledger but
   disappears from active search.

d) **Not worth remembering?** → Skip it. Don't create noise.

You MUST call at least one memory operation (create, update, supersede, or deactivate).
If nothing changed, create a brief observation noting what you saw and why it wasn't significant.

Focus on:
- Decisions made (architectural, personal, strategic)
- Opinions or preferences crystallized
- New projects, tools, or interests
- Relationship updates or patterns
- Status changes on active work
- User instructions (\"remember this\", \"I prefer X\") — these become memories that solidify
DO NOT create memories for:
- Interrupted/failed tool calls with no content
- Pure mechanical events (file saves, test runs without outcome)
- Things already captured in existing memories (update instead of duplicate)

STEP 3 — UPDATE THE MAP:
Write an updated memex as your final message, wrapped in <memex> tags.
The memex is a navigational map, not a status report:
- **Landmarks**: Stable entities with memory IDs — [mem_xxx] Project Name — one-line status
- **Active Trails**: What's hot, where to look — routes to common queries
- **World**: Sources, event counts, last sync
<memex>
# Memex — {user_id}
... updated map ...
</memex>"""


def _should_synthesize(db: SykeDB, user_id: str) -> bool:
    last_ts = db.get_last_synthesis_timestamp(user_id)
    if not last_ts:
        return db.count_events(user_id) >= SYNTHESIS_THRESHOLD

    new_count = db.count_events_since(user_id, last_ts)
    return new_count >= SYNTHESIS_THRESHOLD


def _get_new_events_summary(db: SykeDB, user_id: str, limit: int = 100) -> str:
    last_ts = db.get_last_synthesis_timestamp(user_id)

    if last_ts:
        rows = db.conn.execute(
            """SELECT id, timestamp, source, event_type, title,
                      substr(content, 1, 800) as content_preview
               FROM events WHERE user_id = ? AND ingested_at > ?
               ORDER BY ingested_at ASC LIMIT ?""",
            (user_id, last_ts, limit),
        ).fetchall()
    else:
        rows = db.conn.execute(
            """SELECT id, timestamp, source, event_type, title,
                      substr(content, 1, 800) as content_preview
               FROM events WHERE user_id = ?
               ORDER BY ingested_at ASC LIMIT ?""",
            (user_id, limit),
        ).fetchall()

    if not rows:
        return "[No new events]"

    cols = ["id", "timestamp", "source", "event_type", "title", "content_preview"]
    events = [dict(zip(cols, row)) for row in rows]

    lines = []
    for ev in events:
        lines.append(
            f"### [{ev['source']}] {ev['title'] or ev['event_type']} ({ev['timestamp']})"
        )
        if ev["content_preview"]:
            lines.append(ev["content_preview"])
        lines.append("")

    return "\n".join(lines)


def _extract_memex_content(text: str) -> str | None:
    start = text.find("<memex>")
    end = text.find("</memex>")
    if start == -1 or end == -1:
        return None
    return text[start + len("<memex>") : end].strip()


async def _run_synthesis(db: SykeDB, user_id: str) -> dict:
    memex_content = get_memex_for_injection(db, user_id)
    new_events = _get_new_events_summary(db, user_id)

    prompt = SYNTHESIS_PROMPT.format(
        memex_content=memex_content,
        new_events_summary=new_events,
        user_id=user_id,
    )

    memory_server = build_memory_mcp_server(db, user_id)
    allowed = [f"{MEMORY_PREFIX}{name}" for name in MEMORY_TOOL_NAMES]

    try:
        os.environ.pop("CLAUDECODE", None)
        env_patch: dict[str, str] = {}
        if (Path.home() / ".claude").is_dir():
            env_patch["ANTHROPIC_API_KEY"] = ""

        async def _allow_all(tool_name, tool_input, context=None):
            return PermissionResultAllow()

        options = ClaudeAgentOptions(
            system_prompt=prompt,
            mcp_servers={"memory": memory_server},
            allowed_tools=allowed,
            permission_mode="bypassPermissions",
            max_turns=SYNC_MAX_TURNS,
            max_budget_usd=SYNC_BUDGET,
            model=SYNC_MODEL,
            can_use_tool=_allow_all,
            env=env_patch,
        )

        task = (
            f"Synthesize new events for user '{user_id}' into memories. "
            f"Extract knowledge worth remembering and update the memex."
        )

        answer_parts: list[str] = []
        cost_usd = 0.0
        num_turns = 0

        async with ClaudeSDKClient(options=options) as client:
            await client.query(task)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            answer_parts.append(block.text.strip())
                elif isinstance(message, ResultMessage):
                    cost_usd = message.total_cost_usd or 0.0
                    num_turns = message.num_turns or 0
                    break

        full_response = "\n\n".join(answer_parts)
        new_memex = _extract_memex_content(full_response)
        if new_memex:
            update_memex(db, user_id, new_memex)
            log.info("Memex updated for %s (%d chars)", user_id, len(new_memex))

        db.log_memory_op(
            user_id,
            "synthesize",
            input_summary=f"{len(new_events)} chars of new events",
            output_summary=f"cost=${cost_usd:.4f}, turns={num_turns}, memex_updated={new_memex is not None}",
        )

        return {
            "status": "ok",
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "memex_updated": new_memex is not None,
        }

    except Exception as e:
        log.error("Synthesis failed for %s: %s", user_id, e)
        return {"status": "error", "error": str(e)}


def synthesize(db: SykeDB, user_id: str, force: bool = False) -> dict:
    if not force and not _should_synthesize(db, user_id):
        log.debug("Skipping synthesis for %s (below threshold)", user_id)
        return {"status": "skipped", "reason": "below_threshold"}

    try:
        return asyncio.run(_run_synthesis(db, user_id))
    except Exception as e:
        log.error("Synthesis error for %s: %s", user_id, e)
        return {"status": "error", "error": str(e)}
