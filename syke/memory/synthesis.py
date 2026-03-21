"""Synthesis — extracts memories from new events (Mastra Observer pattern).

Runs after ingestion. Reads new events + memex + recent memories,
uses an agent to extract persistent knowledge, then updates the memex.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
from syke.memory.tools import create_memory_tools
from syke.time import format_for_llm, temporal_grounding_block
from uuid_extensions import uuid7

log = logging.getLogger(__name__)


def _compute_real_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float | None:
    """Compute actual cost from LiteLLM's cost map instead of trusting the SDK.

    The Claude Agent SDK prices tokens using Anthropic's rates regardless of
    which model the LiteLLM proxy actually routes to. This function uses the
    proxy's actual model to look up the correct per-token rates.

    Returns None if the model isn't in the cost map (falls back to SDK cost).
    """
    try:
        import litellm

        entry = litellm.model_cost.get(model)
        if not entry:
            return None

        input_rate = entry.get("input_cost_per_token", 0)
        output_rate = entry.get("output_cost_per_token", 0)
        cache_rate = entry.get("cache_read_input_token_cost", input_rate)

        # Non-cached input tokens = total input - cache hits
        fresh_input = max(0, input_tokens - cache_read_tokens)
        cost = (fresh_input * input_rate) + (cache_read_tokens * cache_rate) + (output_tokens * output_rate)
        return round(cost, 6)
    except Exception:
        return None


def _resolve_proxy_model() -> str | None:
    """Read the actual model from the LiteLLM proxy config."""
    try:
        import yaml

        config_path = Path.home() / ".syke" / "litellm_config.yaml"
        if not config_path.exists():
            return None
        cfg = yaml.safe_load(config_path.read_text())
        for entry in cfg.get("model_list", []):
            model = entry.get("litellm_params", {}).get("model")
            if model:
                return model
    except Exception:
        pass
    return None


def _budget_scale_factor(proxy_model: str) -> float:
    """Compute how much to scale the SDK budget to compensate for pricing mismatch.

    The SDK prices tokens as Sonnet (~$3/M in, $15/M out). If the actual model
    is cheaper, the SDK exhausts the budget too early. Returns the ratio of
    Sonnet cost to actual model cost so the budget can be scaled up.
    """
    try:
        import litellm

        actual = litellm.model_cost.get(proxy_model)
        if not actual:
            return 1.0

        actual_in = actual.get("input_cost_per_token", 0)
        actual_out = actual.get("output_cost_per_token", 0)
        if not actual_in or not actual_out:
            return 1.0

        # Sonnet 4 pricing (what the SDK assumes)
        sonnet_in = 3.0 / 1_000_000   # $3/M
        sonnet_out = 15.0 / 1_000_000  # $15/M

        # Weighted average assuming ~80% input, ~20% output (typical synthesis)
        sonnet_blend = 0.8 * sonnet_in + 0.2 * sonnet_out
        actual_blend = 0.8 * actual_in + 0.2 * actual_out

        if actual_blend <= 0:
            return 1.0

        return sonnet_blend / actual_blend
    except Exception:
        return 1.0

SYNTHESIS_THRESHOLD = 5
MEMORY_PREFIX = "mcp__memory__"
COMMIT_CYCLE_TOOL = "commit_cycle"

_SKILL_DIR = Path(__file__).resolve().parent / "skills"
_SKILL_FILE = _SKILL_DIR / "synthesis.md"
_FALLBACK_PROMPT = "You are Syke's synthesis agent. Create and manage memories from new events. Call commit_cycle when done."


def _load_skill_file(content_override: str | None = None) -> tuple[str, str]:
    """Load skill file content and compute SHA256 hash. Returns (content, hash).

    content_override: if set, use this instead of the file on disk. Used by the
    replay sandbox to inject patched prompts for ablation conditions without
    touching the real skill file or using module-level global state.
    """
    if content_override is not None:
        h = hashlib.sha256(content_override.encode("utf-8")).hexdigest()
        return content_override, h
    try:
        content = _SKILL_FILE.read_text(encoding="utf-8")
        skill_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return content, skill_hash
    except FileNotFoundError:
        log.error("Skill file not found at %s, using fallback", _SKILL_FILE)
        return _FALLBACK_PROMPT, hashlib.sha256(_FALLBACK_PROMPT.encode("utf-8")).hexdigest()


def _make_self_observe_hooks(observer: Any, run_id: str) -> tuple[Any, Any]:
    """Create PreToolUse + PostToolUse hooks that record full tool traces."""
    import time

    from syke.sense.self_observe import SYNTHESIS_TOOL_USE

    _pending_starts: dict[str, float] = {}

    async def _pre_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> SyncHookJSONOutput:
        if tool_use_id:
            _pending_starts[tool_use_id] = time.monotonic()
        return {}

    async def _post_hook(
        input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> SyncHookJSONOutput:
        try:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})
            tool_response = input_data.get("tool_response", "")

            raw_output = str(tool_response) if tool_response else ""
            tool_output = raw_output[:2048]

            start = _pending_starts.pop(tool_use_id or "", 0.0)
            duration_ms = int((time.monotonic() - start) * 1000) if start else 0

            observer.record(
                SYNTHESIS_TOOL_USE,
                {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_output": tool_output,
                    "duration_ms": duration_ms,
                    "success": True,
                },
                run_id=run_id,
            )
        except Exception:
            pass
        return {}

    return _pre_hook, _post_hook


def _build_hooks(observer: Any, run_id: str | None) -> dict[str, list[Any]]:
    hooks: dict[str, list[Any]] = {}
    if observer:
        pre_hook, post_hook = _make_self_observe_hooks(observer, run_id or "unknown")
        hooks["PreToolUse"] = [HookMatcher(hooks=[pre_hook])]
        hooks["PostToolUse"] = [HookMatcher(hooks=[post_hook])]
    return hooks


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

    _CONTENT_SQL = """substr(content, 1, 2000) as content_preview"""

    _SELECT = f"""SELECT id, timestamp, source, event_type, title,
                      role, model, stop_reason, input_tokens, output_tokens,
                      session_id, sequence_index,
                      {_CONTENT_SQL}"""

    last_event_id = db.get_synthesis_cursor(user_id)

    if last_event_id:
        rows = db.conn.execute(
            f"""{_SELECT}
               FROM events WHERE user_id = ? AND id > ?
               ORDER BY id ASC LIMIT ?""",
            (user_id, last_event_id, limit),
        ).fetchall()
    else:
        last_ts = db.get_last_synthesis_timestamp(user_id)

        if last_ts:
            rows = db.conn.execute(
                f"""{_SELECT}
                   FROM events WHERE user_id = ? AND ingested_at > ?
                   ORDER BY ingested_at ASC LIMIT ?""",
                (user_id, last_ts, limit),
            ).fetchall()
        else:
            rows = db.conn.execute(
                f"""{_SELECT}
                   FROM events WHERE user_id = ?
                   ORDER BY ingested_at ASC LIMIT ?""",
                (user_id, limit),
            ).fetchall()

    if not rows:
        return "[No new events]", None

    cols = [
        "id",
        "timestamp",
        "source",
        "event_type",
        "title",
        "role",
        "model",
        "stop_reason",
        "input_tokens",
        "output_tokens",
        "session_id",
        "sequence_index",
        "content_preview",
    ]
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
        header = f"### [{ev['source']}] {ev['title'] or ev['event_type']}"
        if ev.get("role"):
            header += f" ({ev['role']})"
        if ev.get("model"):
            header += f" — {ev['model']}"
        lines.append(f"{header}\n{local_ts}")
        if ev.get("input_tokens"):
            lines.append(f"tokens: in={ev['input_tokens']} out={ev.get('output_tokens', '?')}")
        if ev["content_preview"]:
            lines.append(ev["content_preview"])
        lines.append("")

    return "\n".join(lines), events[-1]["id"]


async def _run_synthesis(
    db: SykeDB,
    user_id: str,
    *,
    observer: Any = None,
    run_id: str | None = None,
    skill_override: str | None = None,
) -> dict[str, object]:
    memex_content = get_memex_for_injection(db, user_id)
    tg = temporal_grounding_block()
    db_file = str(db.db_path)

    cursor_id = db.get_synthesis_cursor(user_id) or ""
    # Exclude source='syke' (self-observation traces) from pending count and cursor.
    # Traces stay in the DB for observability but must not pollute the synthesis
    # input window — otherwise the agent processes its own exhaust each cycle.
    if cursor_id:
        pending_count = db.count_events_after_id(user_id, cursor_id, exclude_source="syke")
    else:
        pending_count = db.count_events(user_id) - db.count_events(user_id, source="syke")

    newest_row = db.conn.execute(
        "SELECT id FROM events WHERE user_id = ? AND source != 'syke' ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    new_cursor = newest_row[0] if newest_row else cursor_id

    skill_content, skill_hash = _load_skill_file(skill_override)
    cycle_id = db.insert_cycle_record(
        user_id, cursor_start=cursor_id, skill_hash=skill_hash, model=SYNC_MODEL
    )

    backlog_stats = (
        f"Pending events since last synthesis: {pending_count}\n"
        f"Cursor (last processed event ID): {cursor_id or 'none — first run'}\n"
        f"Query events with: id > '{cursor_id}' (or all events if first run)"
    )

    first_run = db.get_memex(user_id) is None
    max_turns = SETUP_SYNC_MAX_TURNS if first_run else SYNC_MAX_TURNS
    budget = SETUP_SYNC_BUDGET if first_run else SYNC_BUDGET

    # The SDK enforces max_budget_usd using Anthropic Sonnet pricing, but the
    # LiteLLM proxy may route to a cheaper model (e.g. GPT-5 Mini).  Scale
    # the budget so the SDK doesn't kill the run prematurely.
    proxy_model = _resolve_proxy_model()
    if proxy_model:
        _sdk_budget_scale = _budget_scale_factor(proxy_model)
        if _sdk_budget_scale > 1:
            budget = budget * _sdk_budget_scale
            log.debug("Budget scaled %.1fx → $%.2f for %s", _sdk_budget_scale, budget, proxy_model)

    runtime_context = (
        f"\n---\n\n## Runtime Context\n\n"
        f"### Current Memex\n{memex_content or '[No memex yet]'}\n\n"
        f"### Evidence Layer\n"
        f"Database path: {db_file}\n"
        f'Query with: sqlite3 {db_file} "YOUR SQL HERE"\n\n'
        f"{backlog_stats}\n\n"
        f"Events schema columns: id, timestamp, source, event_type, title, role, model, content, "
        f"stop_reason, input_tokens, output_tokens, session_id, sequence_index, "
        f"parent_event_id, external_id, ingested_at, user_id\n\n"
        f"SQL examples:\n"
        f"  sqlite3 {db_file} \"SELECT source, COUNT(*) FROM events WHERE id > '{cursor_id}' GROUP BY source\"\n"
        f"  sqlite3 {db_file} \"SELECT session_id, COUNT(*) as turns FROM events WHERE id > '{cursor_id}' GROUP BY session_id ORDER BY turns DESC LIMIT 10\"\n\n"
        f"{tg}\n"
    )
    prompt = skill_content + runtime_context

    committed: dict[str, Any] | None = None

    @tool(
        COMMIT_CYCLE_TOOL,
        "Commit this synthesis cycle. Call exactly once when done. status='completed' with content (full rewritten memex) on success, or status='failed' on failure. hints is optional free-text (max 500 chars).",
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["completed", "failed"],
                },
                "content": {
                    "type": "string",
                    "description": "Full rewritten memex content when status is completed",
                },
                "hints": {
                    "type": "string",
                    "description": "Free-text hints for future cycles (max 500 chars, stored, never parsed)",
                },
            },
            "required": ["status"],
        },
    )
    async def commit_cycle_fn(args: dict[str, Any]) -> dict[str, Any]:
        nonlocal committed
        committed = dict(args)
        return {"content": [{"type": "text", "text": "cycle committed"}]}

    memory_tools = create_memory_tools(db, user_id)
    memory_server = create_sdk_mcp_server(
        name="memory",
        version="1.0.0",
        tools=[*memory_tools, commit_cycle_fn],
    )
    allowed = [
        "Bash",
        "Read",
        "Write",
        "Grep",
        "Glob",
        f"{MEMORY_PREFIX}memory_write",
        f"{MEMORY_PREFIX}{COMMIT_CYCLE_TOOL}",
    ]

    try:
        agent_env = build_agent_env()

        with clean_claude_env():
            options_kwargs: dict[str, Any] = dict(
                system_prompt=prompt,
                mcp_servers={"memory": memory_server},
                allowed_tools=allowed,
                permission_mode="bypassPermissions",
                max_turns=max_turns,
                max_budget_usd=budget,
                model=SYNC_MODEL,
                include_partial_messages=True,
                env=agent_env,
                hooks=_build_hooks(observer, run_id),
                thinking={"type": "enabled", "budget_tokens": SYNC_THINKING},
            )

            options = ClaudeAgentOptions(**options_kwargs)

            task = (
                f"Synthesize new events for user '{user_id}' into memories. "
                f"Extract knowledge worth remembering and update the memex. "
                f"Call commit_cycle when done."
            )

            cost_usd = 0.0
            num_turns = 0
            input_tokens = 0
            output_tokens = 0
            cache_read_tokens = 0
            duration_api_ms = 0
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
                                    bare = block.name.removeprefix(MEMORY_PREFIX)
                                    if bare == COMMIT_CYCLE_TOOL and committed is None:
                                        committed = (
                                            dict(block.input)
                                            if isinstance(block.input, dict)
                                            else {}
                                        )
                                    outcome_key = _TOOL_OUTCOME_MAP.get(
                                        block.name.removeprefix("mcp__syke__")
                                    )
                                    if outcome_key:
                                        outcome_counts[outcome_key] += 1
                        elif isinstance(message, ResultMessage):
                            sdk_cost = message.total_cost_usd or 0.0
                            num_turns = message.num_turns or 0
                            usage = getattr(message, "usage", None) or {}
                            input_tokens = usage.get("input_tokens", 0)
                            output_tokens = usage.get("output_tokens", 0)
                            cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                            duration_api_ms = getattr(message, "duration_api_ms", 0) or 0

                            # Recompute cost from actual proxy model rates when
                            # we have token data (SDK uses Anthropic pricing).
                            real_cost = None
                            if (input_tokens or output_tokens) and proxy_model:
                                real_cost = _compute_real_cost(
                                    proxy_model, input_tokens, output_tokens, cache_read_tokens
                                )
                            if real_cost is not None:
                                cost_usd = real_cost
                                log.debug(
                                    "Cost corrected: SDK=$%.4f → real=$%.4f (model=%s)",
                                    sdk_cost, real_cost, proxy_model,
                                )
                            else:
                                cost_usd = sdk_cost
                            break
                except ClaudeSDKError as stream_err:
                    if "Unknown message type" not in str(stream_err):
                        raise
                    log.warning("Synthesis stream interrupted: %s", stream_err)

            if committed is not None:
                if committed["status"] == "completed":
                    memex_updated = False
                    if committed.get("content"):
                        content = str(committed["content"]).strip()
                        if content:
                            update_memex(db, user_id, content)
                            memex_updated = True
                            log.info("Memex updated for %s (%d chars)", user_id, len(content))
                    if new_cursor:
                        db.set_synthesis_cursor(user_id, new_cursor)
                    if committed.get("hints"):
                        hints_text = str(committed["hints"])[:500]
                        db.insert_cycle_annotation(cycle_id, "synthesis", "hints", hints_text)
                    db.complete_cycle_record(
                        cycle_id,
                        status="completed",
                        cursor_end=new_cursor,
                        events_processed=pending_count,
                        memories_created=outcome_counts["created"],
                        memories_updated=outcome_counts.get("superseded", 0),
                        links_created=outcome_counts.get("linked", 0),
                        memex_updated=1 if committed.get("content") else 0,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read_tokens,
                        duration_ms=duration_api_ms,
                    )
                    return {
                        "status": "ok",
                        "cost_usd": cost_usd,
                        "num_turns": num_turns,
                        "memex_updated": memex_updated,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "duration_ms": duration_api_ms,
                    }
                else:
                    if committed.get("hints"):
                        hints_text = str(committed["hints"])[:500]
                        db.insert_cycle_annotation(cycle_id, "synthesis", "hints", hints_text)
                    db.complete_cycle_record(
                        cycle_id, status="failed",
                        cost_usd=cost_usd, input_tokens=input_tokens,
                        output_tokens=output_tokens, duration_ms=duration_api_ms,
                    )
                    return {
                        "status": "failed",
                        "cost_usd": cost_usd,
                        "num_turns": num_turns,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "error": "Synthesis failed via commit_cycle",
                    }

            log.error(
                "Synthesis for %s did not call commit_cycle "
                "(model=%s, turns=%d, cost=$%.4f, tool_calls=%d)",
                user_id,
                SYNC_MODEL,
                num_turns,
                cost_usd,
                tool_call_count,
            )
            db.complete_cycle_record(
                cycle_id, status="incomplete",
                cost_usd=cost_usd, input_tokens=input_tokens,
                output_tokens=output_tokens, duration_ms=duration_api_ms,
            )
            return {
                "status": "incomplete",
                "cost_usd": cost_usd,
                "num_turns": num_turns,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "error": "synthesis did not call commit_cycle",
            }

    except Exception as e:
        log.error("Synthesis failed for %s: %s", user_id, e)
        return {"status": "error", "error": str(e)}


async def _run_synthesis_with_timeout(
    db: SykeDB,
    user_id: str,
    *,
    observer: Any = None,
    run_id: str | None = None,
    skill_override: str | None = None,
) -> dict[str, object]:
    try:
        return await asyncio.wait_for(
            _run_synthesis(db, user_id, observer=observer, run_id=run_id, skill_override=skill_override),
            timeout=SYNC_TIMEOUT,
        )
    except TimeoutError:
        log.error("Synthesis timed out for %s after %ds", user_id, SYNC_TIMEOUT)
        return {"status": "error", "error": f"Timed out after {SYNC_TIMEOUT}s"}


def synthesize(
    db: SykeDB, user_id: str, force: bool = False, skill_override: str | None = None
) -> dict[str, object]:
    result: dict[str, object]
    observer_api = importlib.import_module("syke.sense.self_observe")
    observer = observer_api.SykeObserver(db, user_id)
    run_id = str(uuid7())
    started_at = datetime.now(UTC)
    observer.record(
        observer_api.SYNTHESIS_START,
        {"start_time": started_at.isoformat()},
        run_id=run_id,
    )

    if not force and not _should_synthesize(db, user_id):
        log.debug("Skipping synthesis for %s (below threshold)", user_id)
        ended_at = datetime.now(UTC)
        observer.record(
            observer_api.SYNTHESIS_SKIPPED,
            {
                "start_time": started_at.isoformat(),
                "end_time": ended_at.isoformat(),
                "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                "events_count": 0,
                "cost_usd": 0.0,
                "reason": "below_threshold",
            },
            run_id=run_id,
        )
        return {"status": "skipped", "reason": "below_threshold"}

    try:
        result = asyncio.run(
            _run_synthesis_with_timeout(db, user_id, observer=observer, run_id=run_id, skill_override=skill_override)
        )
    except Exception as e:
        log.error("Synthesis error for %s: %s", user_id, e)
        result = {"status": "error", "error": str(e)}

    ended_at = datetime.now(UTC)
    observer.record(
        observer_api.SYNTHESIS_COMPLETE,
        {
            "start_time": started_at.isoformat(),
            "end_time": ended_at.isoformat(),
            "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
            "events_count": result.get("events_count", 0),
            "cost_usd": result.get("cost_usd", 0.0),
            "status": result.get("status", "unknown"),
            "error": result.get("error"),
        },
        run_id=run_id,
    )
    return cast(dict[str, object], result)
