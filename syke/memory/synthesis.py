"""Synthesis — extracts memories from new events (Mastra Observer pattern).

Runs after ingestion. Reads new events + memex + recent memories,
uses an agent to extract persistent knowledge, then updates the memex.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    ResultMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import HookContext, HookInput, StreamEvent, SyncHookJSONOutput

from syke.config import (
    SETUP_SYNC_BUDGET,
    SETUP_SYNC_MAX_TURNS,
    SYNC_BUDGET,
    SYNC_MAX_TURNS,
    SYNC_MODEL,
    SYNC_THINKING,
    SYNC_TIMEOUT,
    clean_claude_env,
)
from syke.db import SykeDB
from syke.llm import build_agent_env
from syke.memory.memex import (
    get_memex_for_injection,
    update_memex,
)
from syke.memory.tools import MEMORY_TOOL_NAMES, create_memory_tools
from syke.time import format_for_llm, temporal_grounding_block

log = logging.getLogger(__name__)

SYNTHESIS_THRESHOLD = 5
MEMORY_PREFIX = "mcp__memory__"
FINALIZE_MEMEX_TOOL = "finalize_memex"

SYNTHESIS_PROMPT = """You are Syke's memory synthesizer. You maintain a living map of
who this person is — through memories you create, update, and connect.

CRITICAL CONTRACT: When you finish, you MUST call the finalize_memex tool exactly once.
Reserve your last turn for it. If nothing changed, call it with status='unchanged' immediately.
- status='updated' + full rewritten memex content when the memex should change.
- status='unchanged' when the current memex should stay as-is.
- Do not wrap the memex in XML or markdown code fences.

## Current Memex
{memex_content}
{new_events_summary}
Read the memex first. It's your map — what's stable, what's moving, what's context.
Then process the new events against what is already known.
For each event worth remembering:
- New knowledge: call create_memory. Write it as a story, not a fact list.
- Updates existing knowledge: call update_memory or supersede_memory.
- Makes older knowledge obsolete: call deactivate_memory.
- Connects to related knowledge: call create_link.
- Not worth remembering: skip.
Prioritize decisions, durable preferences, ongoing work, and relationship changes.
Then rewrite the memex. The memex is a map, not a report:
- Stable things anchor it (people, projects, settled decisions).
- Active things show where movement is (what's hot, what just changed).
- Point to memories when details exist — the map routes, the memories hold the story.
- Context grounds it (sources, time, world state).
{temporal_context}
Time matters: start from now, then recent, then settled.
When writing temporal references, use anchored local time (e.g., '~6–9 PM PST (02:00–05:00Z)').
Do not infer time-of-day from raw UTC — use the local timestamps provided with each event.
Structure emerges from what matters to this person — not from a template.
Remember: call finalize_memex exactly once when done. Do not end without calling it."""


async def _enforce_finalize_memex(
    input_data: HookInput, tool_use_id: str | None, context: HookContext
) -> SyncHookJSONOutput:
    if input_data.get("stop_hook_active"):
        return {}

    import json
    from pathlib import Path

    transcript_path = input_data.get("transcript_path", "")
    try:
        for line in Path(transcript_path).read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            for block in entry.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("name") == FINALIZE_MEMEX_TOOL:
                    return {}
    except Exception:
        return {}

    return {
        "decision": "block",
        "reason": (
            "You have not called finalize_memex yet. "
            "You MUST call it now with status='updated' and the full rewritten memex, "
            "or status='unchanged' if nothing changed."
        ),
    }


class SynthesisIncompleteError(RuntimeError):
    pass


def _finalize_memex_result(args: dict[str, Any]) -> tuple[bool, str | None]:
    status = args.get("status")
    content = args.get("content")

    if status not in {"updated", "unchanged"}:
        raise SynthesisIncompleteError("finalize_memex returned invalid status")

    if status == "unchanged":
        return False, None

    if not isinstance(content, str) or not content.strip():
        raise SynthesisIncompleteError("finalize_memex requires non-empty content for updated")

    return True, content.strip()


def _should_synthesize(db: SykeDB, user_id: str) -> bool:
    last_ts = db.get_last_synthesis_timestamp(user_id)
    if not last_ts:
        return db.count_events(user_id) >= SYNTHESIS_THRESHOLD

    new_count = db.count_events_since(user_id, last_ts)
    if new_count >= SYNTHESIS_THRESHOLD:
        return True

    last_event_id = db.get_synthesis_cursor(user_id)
    if not last_event_id:
        return False

    pending_count = db.count_events_after_id(user_id, last_event_id)
    backlog_count = max(0, pending_count - new_count)
    return backlog_count > 0


def _get_new_events_summary(
    db: SykeDB,
    user_id: str,
    limit: int | None = None,
) -> tuple[str, str | None]:
    from syke.config import SYNTHESIS_EVENT_LIMIT

    if limit is None:
        limit = SYNTHESIS_EVENT_LIMIT

    _CONTENT_SQL = """
        CASE WHEN event_type = 'session'
             THEN substr(content, 1, 800)
             ELSE content
        END as content_preview"""

    last_event_id = db.get_synthesis_cursor(user_id)

    if last_event_id:
        rows = db.conn.execute(
            f"""SELECT id, timestamp, source, event_type, title,
                      {_CONTENT_SQL}
               FROM events WHERE user_id = ? AND id > ?
               ORDER BY id ASC LIMIT ?""",
            (user_id, last_event_id, limit),
        ).fetchall()
    else:
        last_ts = db.get_last_synthesis_timestamp(user_id)

        if last_ts:
            rows = db.conn.execute(
                f"""SELECT id, timestamp, source, event_type, title,
                          {_CONTENT_SQL}
                   FROM events WHERE user_id = ? AND ingested_at > ?
                   ORDER BY ingested_at ASC LIMIT ?""",
                (user_id, last_ts, limit),
            ).fetchall()
        else:
            rows = db.conn.execute(
                f"""SELECT id, timestamp, source, event_type, title,
                          {_CONTENT_SQL}
                   FROM events WHERE user_id = ?
                   ORDER BY ingested_at ASC LIMIT ?""",
                (user_id, limit),
            ).fetchall()

    if not rows:
        return "[No new events]", None

    cols = ["id", "timestamp", "source", "event_type", "title", "content_preview"]
    events = [dict(zip(cols, row, strict=False)) for row in rows]

    total_chars = sum(len(ev["content_preview"] or "") for ev in events)
    total_tokens_est = total_chars // 4
    log.info(
        "Synthesis input: %d events, %d chars (~%d tokens_est)",
        len(events),
        total_chars,
        total_tokens_est,
    )

    lines = []
    for ev in events:
        local_ts = format_for_llm(ev["timestamp"])
        lines.append(f"### [{ev['source']}] {ev['title'] or ev['event_type']}\n{local_ts}")
        if ev["content_preview"]:
            lines.append(ev["content_preview"])
        lines.append("")

    return "\n".join(lines), events[-1]["id"]


async def _run_synthesis(db: SykeDB, user_id: str) -> dict[str, object]:
    memex_content = get_memex_for_injection(db, user_id)
    summary, new_cursor = _get_new_events_summary(db, user_id)
    tg = temporal_grounding_block()

    first_run = db.get_memex(user_id) is None
    max_turns = SETUP_SYNC_MAX_TURNS if first_run else SYNC_MAX_TURNS
    budget = SETUP_SYNC_BUDGET if first_run else SYNC_BUDGET

    prompt = SYNTHESIS_PROMPT.format(
        memex_content=memex_content or "[No memex yet]",
        new_events_summary=f"\n## New Events\n{summary}",
        temporal_context=tg,
        user_id=user_id,
    )

    finalized: dict[str, Any] | None = None

    @tool(
        FINALIZE_MEMEX_TOOL,
        "Finalize the rewritten memex after synthesis. Call exactly once with updated content or unchanged status.",
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["updated", "unchanged"],
                    "description": "Whether the memex should be updated or left unchanged",
                },
                "content": {
                    "type": "string",
                    "description": "Full rewritten memex content when status is updated",
                },
            },
            "required": ["status"],
        },
    )
    async def finalize_memex(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal finalized
        finalized = dict(args)
        return {"content": [{"type": "text", "text": "memex finalized"}]}

    memory_tools = create_memory_tools(db, user_id)
    memory_server = create_sdk_mcp_server(
        name="memory",
        version="1.0.0",
        tools=[*memory_tools, finalize_memex],
    )
    allowed = [f"{MEMORY_PREFIX}{name}" for name in MEMORY_TOOL_NAMES]
    allowed.append(f"{MEMORY_PREFIX}{FINALIZE_MEMEX_TOOL}")

    try:
        with clean_claude_env():
            options = ClaudeAgentOptions(
                system_prompt=prompt,
                mcp_servers={"memory": memory_server},
                allowed_tools=allowed,
                permission_mode="bypassPermissions",
                max_turns=max_turns,
                max_budget_usd=budget,
                model=SYNC_MODEL,
                include_partial_messages=True,
                thinking={"type": "enabled", "budget_tokens": SYNC_THINKING},
                env=build_agent_env(),
                hooks={
                    "Stop": [HookMatcher(hooks=[_enforce_finalize_memex])],
                },
            )

            task = (
                f"Synthesize new events for user '{user_id}' into memories. "
                f"Extract knowledge worth remembering and update the memex. "
                f"You MUST call finalize_memex exactly once when done."
            )

            cost_usd = 0.0
            num_turns = 0
            tool_call_count = 0
            outcome_counts: dict[str, int] = {
                "created": 0,
                "superseded": 0,
                "linked": 0,
                "deactivated": 0,
            }
            _TOOL_OUTCOME_MAP = {
                "create_memory": "created",
                "supersede_memory": "superseded",
                "create_link": "linked",
                "deactivate_memory": "deactivated",
            }

            async with ClaudeSDKClient(options=options) as client:
                await client.query(task)
                try:
                    async for message in client.receive_response():
                        if isinstance(message, StreamEvent):
                            continue  # tolerate streaming events from proxy
                        elif isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, ToolUseBlock):
                                    tool_call_count += 1
                                    outcome_key = _TOOL_OUTCOME_MAP.get(
                                        block.name.removeprefix("mcp__syke__")
                                    )
                                    if outcome_key:
                                        outcome_counts[outcome_key] += 1
                                    if block.name == FINALIZE_MEMEX_TOOL:
                                        finalized = dict(block.input or {})
                        elif isinstance(message, ResultMessage):
                            cost_usd = message.total_cost_usd or 0.0
                            num_turns = message.num_turns or 0
                            break
                except ClaudeSDKError as stream_err:
                    if "Unknown message type" not in str(stream_err):
                        raise
                    log.warning("Synthesis stream interrupted: %s", stream_err)

            if finalized is None:
                log.error(
                    "Synthesis for %s did not call finalize_memex "
                    "(model=%s, turns=%d, cost=$%.4f, tool_calls=%d)",
                    user_id,
                    SYNC_MODEL,
                    num_turns,
                    cost_usd,
                    tool_call_count,
                )
                raise SynthesisIncompleteError("synthesis did not call finalize_memex")

            memex_updated, new_memex = _finalize_memex_result(finalized)
            if memex_updated and new_memex is not None:
                update_memex(db, user_id, new_memex)
                log.info("Memex updated for %s (%d chars)", user_id, len(new_memex))

            if new_cursor:
                db.set_synthesis_cursor(user_id, new_cursor)

            db.log_memory_op(
                user_id,
                "synthesize",
                input_summary=f"{len(summary)} chars of new events",
                output_summary=f"cost=${cost_usd:.4f}, turns={num_turns}, memex_updated={memex_updated}",
                metadata={
                    **outcome_counts,
                    "events_processed": len(summary),
                    "memex_updated": memex_updated,
                    "cost_usd": cost_usd,
                    "tool_calls": tool_call_count,
                    "turns": num_turns,
                },
            )

            return {
                "status": "ok",
                "cost_usd": cost_usd,
                "num_turns": num_turns,
                "memex_updated": memex_updated,
            }

    except Exception as e:
        log.error("Synthesis failed for %s: %s", user_id, e)
        return {"status": "error", "error": str(e)}


async def _run_synthesis_with_timeout(db: SykeDB, user_id: str) -> dict[str, object]:
    try:
        return await asyncio.wait_for(
            _run_synthesis(db, user_id),
            timeout=SYNC_TIMEOUT,
        )
    except TimeoutError:
        log.error("Synthesis timed out for %s after %ds", user_id, SYNC_TIMEOUT)
        return {"status": "error", "error": f"Timed out after {SYNC_TIMEOUT}s"}


def synthesize(db: SykeDB, user_id: str, force: bool = False) -> dict[str, object]:
    if not force and not _should_synthesize(db, user_id):
        log.debug("Skipping synthesis for %s (below threshold)", user_id)
        return {"status": "skipped", "reason": "below_threshold"}

    try:
        return asyncio.run(_run_synthesis_with_timeout(db, user_id))
    except Exception as e:
        log.error("Synthesis error for %s: %s", user_id, e)
        return {"status": "error", "error": str(e)}
