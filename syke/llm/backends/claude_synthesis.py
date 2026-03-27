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
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    StreamEvent,
    SyncHookJSONOutput,
    UserMessage,
)

from syke.config import (
    SETUP_SYNC_BUDGET,
    SETUP_SYNC_MAX_TURNS,
    SYNC_BUDGET,
    SYNC_EVENT_THRESHOLD,
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
from syke.time import temporal_grounding_block
from uuid_extensions import uuid7
from syke.llm.backends.claude_common import (
    _budget_scale_factor,
    _compute_real_cost,
    _load_skill_file,
    _resolve_proxy_model,
)

log = logging.getLogger(__name__)


MEMORY_PREFIX = "mcp__memory__"
COMMIT_CYCLE_TOOL = "commit_cycle"


def _make_self_observe_hooks(observer: Any, run_id: str) -> tuple[Any, Any]:
    """Create PreToolUse + PostToolUse hooks that record full tool traces."""
    import time

    from syke.observe.trace import SYNTHESIS_TOOL_USE

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
        return db.count_events(user_id) >= SYNC_EVENT_THRESHOLD

    new_count = db.count_events_since(user_id, last_ts)
    if new_count >= SYNC_EVENT_THRESHOLD:
        return True

    last_event_id = db.get_synthesis_cursor(user_id)
    if not last_event_id:
        return False

    pending_count = db.count_events_after_id(user_id, last_event_id)
    backlog_count = max(0, pending_count - new_count)
    return backlog_count > 0


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

    # Runtime context is built after sandbox setup (db_file may be symlinked)

    committed: dict[str, Any] | None = None

    @tool(
        COMMIT_CYCLE_TOOL,
        "Commit when done. status='completed' with content (full rewritten document), or status='failed'. hints is optional free-text (max 500 chars).",
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

    # ── memory_write: create / update / supersede / deactivate / link ──
    from syke.models import Memory, Link
    import uuid
    import re as _re

    @tool(
        "memory_write",
        "Create, update, supersede, deactivate memories or link them. "
        "op='create': content required, returns new memory ID. "
        "op='update': memory_id + content required. "
        "op='supersede': memory_id + content required (old deactivated, new created). "
        "op='deactivate': memory_id required. "
        "op='link': source_id + target_id + reason required.",
        {
            "type": "object",
            "properties": {
                "op": {
                    "type": "string",
                    "enum": ["create", "update", "supersede", "deactivate", "link"],
                },
                "content": {
                    "type": "string",
                    "description": "Memory content (for create/update/supersede)",
                },
                "memory_id": {
                    "type": "string",
                    "description": "Existing memory ID (for update/supersede/deactivate)",
                },
                "source_id": {"type": "string", "description": "Source memory ID (for link)"},
                "target_id": {"type": "string", "description": "Target memory ID (for link)"},
                "reason": {"type": "string", "description": "Link reason (for link)"},
            },
            "required": ["op"],
        },
    )
    async def memory_write_fn(args: dict[str, Any]) -> dict[str, Any]:
        op = args["op"]
        try:
            if op == "create":
                mem = Memory(
                    id=str(uuid.uuid7()),
                    user_id=user_id,
                    content=args.get("content", ""),
                )
                mid = db.insert_memory(mem)
                return {"content": [{"type": "text", "text": f"created {mid}"}]}
            elif op == "update":
                ok = db.update_memory(user_id, args["memory_id"], args.get("content", ""))
                return {"content": [{"type": "text", "text": f"updated={ok}"}]}
            elif op == "supersede":
                new_mem = Memory(
                    id=str(uuid.uuid7()),
                    user_id=user_id,
                    content=args.get("content", ""),
                )
                new_id = db.supersede_memory(user_id, args["memory_id"], new_mem)
                return {"content": [{"type": "text", "text": f"superseded → {new_id}"}]}
            elif op == "deactivate":
                ok = db.deactivate_memory(user_id, args["memory_id"])
                return {"content": [{"type": "text", "text": f"deactivated={ok}"}]}
            elif op == "link":
                lnk = Link(
                    id=str(uuid.uuid7()),
                    user_id=user_id,
                    source_id=args["source_id"],
                    target_id=args["target_id"],
                    reason=args.get("reason", ""),
                )
                lid = db.insert_link(lnk)
                return {"content": [{"type": "text", "text": f"linked {lid}"}]}
            else:
                return {"content": [{"type": "text", "text": f"unknown op: {op}"}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}]}

    # ── search_memories: FTS5 search with sanitized query ──
    @tool(
        "search_memories",
        "Search active memories by keyword (FTS5/BM25). Returns up to 10 matches with IDs and content.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    )
    async def search_memories_fn(args: dict[str, Any]) -> dict[str, Any]:
        raw_query = args["query"]
        # Sanitize for FTS5: keep only alphanumeric and spaces
        sanitized = _re.sub(r"[^a-zA-Z0-9\s]", "", raw_query).strip()
        if not sanitized:
            return {"content": [{"type": "text", "text": "no matches (empty query)"}]}
        # Quote each word to avoid FTS5 syntax collisions with SQL keywords
        words = sanitized.split()
        fts_query = " ".join(f'"{w}"' for w in words[:10])
        try:
            results = db.search_memories(user_id, fts_query, limit=10)
        except Exception:
            # Fallback: try first 3 words only
            try:
                results = db.search_memories(
                    user_id, " ".join(f'"{w}"' for w in words[:3]), limit=10
                )
            except Exception:
                return {"content": [{"type": "text", "text": "search failed"}]}
        if not results:
            return {"content": [{"type": "text", "text": "no matches"}]}
        lines = []
        for r in results:
            mid = r["id"][:12]
            content = r.get("content", "")[:200]
            lines.append(f"{mid}: {content}")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    commit_server = create_sdk_mcp_server(
        name="memory",
        version="1.0.0",
        tools=[commit_cycle_fn, memory_write_fn, search_memories_fn],
    )
    allowed = [
        "Bash",
        "Read",
        "Write",
        "Grep",
        "Glob",
        f"{MEMORY_PREFIX}{COMMIT_CYCLE_TOOL}",
        f"{MEMORY_PREFIX}memory_write",
        f"{MEMORY_PREFIX}search_memories",
    ]

    try:
        agent_env = build_agent_env()

        with clean_claude_env():
            # Run from an isolated temp dir so the Claude CLI subprocess
            # doesn't read .claude/CLAUDE.md, project memory, or commands
            # from the repo root. The agent should only see what we give it.
            import tempfile

            sandbox_cwd = tempfile.mkdtemp(prefix="syke_sandbox_")

            # Symlink DB into sandbox dir with neutral name so the agent
            # never sees experiment paths, condition names, or directory structure.
            import os

            sandbox_db = os.path.join(sandbox_cwd, "events.db")
            os.symlink(os.path.abspath(db_file), sandbox_db)
            db_file = sandbox_db  # All SQL commands now use this neutral path

            # Build runtime context with the sanitized db path
            memex_chars = len(memex_content) if memex_content else 0
            mem_count = db.count_memories(user_id, active_only=True)
            link_count = db.conn.execute(
                "SELECT COUNT(*) FROM links WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            runtime_context = (
                f"\n---\n\n## Runtime Context\n\n"
                f"### Your State\n"
                f"Memex: {memex_chars} chars | Memories: {mem_count} active | Links: {link_count}\n\n"
                f"### Current Document\n{memex_content or '[Empty]'}\n\n"
                f"### Data\n"
                f"Database: events.db\n"
                f'Query: sqlite3 {db_file} "YOUR SQL HERE"\n'
                f"**Use `<>` not `!=` in sqlite3 shell queries.**\n\n"
                f"{backlog_stats}\n\n"
                f"Schema: id, timestamp, source, event_type, title, role, model, content, "
                f"stop_reason, input_tokens, output_tokens, session_id, sequence_index, "
                f"parent_event_id, external_id, ingested_at, user_id\n\n"
                f"Examples:\n"
                f"  sqlite3 {db_file} \"SELECT source, COUNT(*) FROM events WHERE id > '{cursor_id}' AND source <> 'syke' GROUP BY source\"\n"
                f"  sqlite3 {db_file} \"SELECT session_id, COUNT(*) as turns FROM events WHERE id > '{cursor_id}' AND source <> 'syke' GROUP BY session_id ORDER BY turns DESC LIMIT 10\"\n\n"
                f"{tg}\n"
            )
            prompt = skill_content + runtime_context

            options_kwargs: dict[str, Any] = dict(
                system_prompt=prompt,
                mcp_servers={"memory": commit_server},
                allowed_tools=allowed,
                permission_mode="bypassPermissions",
                max_turns=max_turns,
                max_budget_usd=budget,
                model=SYNC_MODEL,
                include_partial_messages=True,
                env=agent_env,
                hooks=_build_hooks(observer, run_id),
                thinking={"type": "enabled", "budget_tokens": SYNC_THINKING},
                cwd=sandbox_cwd,
            )

            options = ClaudeAgentOptions(**options_kwargs)

            task = "Execute your synthesis prompt."

            cost_usd = 0.0
            num_turns = 0
            input_tokens = 0
            output_tokens = 0
            cache_read_tokens = 0
            duration_api_ms = 0
            tool_call_count = 0
            transcript: list[dict[str, Any]] = []

            async with ClaudeSDKClient(options=options) as client:
                await client.query(task)
                try:
                    async for message in client.receive_response():
                        if isinstance(message, StreamEvent):
                            continue
                        elif isinstance(message, AssistantMessage):
                            turn_record: dict[str, Any] = {"role": "assistant", "blocks": []}
                            for block in message.content:
                                if isinstance(block, ThinkingBlock):
                                    turn_record["blocks"].append(
                                        {
                                            "type": "thinking",
                                            "text": block.thinking[:4000],
                                        }
                                    )
                                elif isinstance(block, TextBlock):
                                    turn_record["blocks"].append(
                                        {
                                            "type": "text",
                                            "text": block.text[:2000],
                                        }
                                    )
                                elif isinstance(block, ToolUseBlock):
                                    tool_call_count += 1
                                    bare = block.name.removeprefix(MEMORY_PREFIX)
                                    tool_input = (
                                        dict(block.input) if isinstance(block.input, dict) else {}
                                    )
                                    turn_record["blocks"].append(
                                        {
                                            "type": "tool_use",
                                            "name": block.name,
                                            "input": {
                                                k: (str(v)[:500] if isinstance(v, str) else v)
                                                for k, v in tool_input.items()
                                            },
                                        }
                                    )
                                    if bare == COMMIT_CYCLE_TOOL and committed is None:
                                        committed = tool_input
                                elif isinstance(block, ToolResultBlock):
                                    result_content = block.content
                                    if isinstance(result_content, list):
                                        result_text = " ".join(
                                            str(item.get("text", ""))
                                            if isinstance(item, dict)
                                            else str(item)
                                            for item in result_content
                                        )[:2000]
                                    else:
                                        result_text = (
                                            str(result_content)[:2000] if result_content else ""
                                        )
                                    turn_record["blocks"].append(
                                        {
                                            "type": "tool_result",
                                            "tool_use_id": block.tool_use_id,
                                            "content": result_text,
                                            "is_error": block.is_error or False,
                                        }
                                    )
                            transcript.append(turn_record)
                        elif isinstance(message, UserMessage):
                            user_record: dict[str, Any] = {"role": "user", "blocks": []}
                            content = message.content
                            if isinstance(content, str):
                                user_record["blocks"].append(
                                    {
                                        "type": "text",
                                        "text": content[:2000],
                                    }
                                )
                            elif isinstance(content, list):
                                for block in content:
                                    if isinstance(block, ToolResultBlock):
                                        rc = block.content
                                        if isinstance(rc, list):
                                            rt = " ".join(
                                                str(i.get("text", ""))
                                                if isinstance(i, dict)
                                                else str(i)
                                                for i in rc
                                            )[:2000]
                                        else:
                                            rt = str(rc)[:2000] if rc else ""
                                        user_record["blocks"].append(
                                            {
                                                "type": "tool_result",
                                                "tool_use_id": block.tool_use_id,
                                                "content": rt,
                                                "is_error": block.is_error or False,
                                            }
                                        )
                                    elif isinstance(block, TextBlock):
                                        user_record["blocks"].append(
                                            {
                                                "type": "text",
                                                "text": block.text[:2000],
                                            }
                                        )
                            if message.tool_use_result:
                                user_record["blocks"].append(
                                    {
                                        "type": "tool_result_legacy",
                                        "content": str(message.tool_use_result)[:2000],
                                    }
                                )
                            if user_record["blocks"]:
                                transcript.append(user_record)
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
                                    sdk_cost,
                                    real_cost,
                                    proxy_model,
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
                        memories_created=0,
                        memories_updated=0,
                        links_created=0,
                        memex_updated=1 if committed.get("content") else 0,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_read_tokens=cache_read_tokens,
                        duration_ms=duration_api_ms,
                    )
                    return {
                        "status": "completed",
                        "backend": "claude",
                        "cost_usd": cost_usd,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "duration_ms": duration_api_ms,
                        "events_processed": pending_count,
                        "memex_updated": memex_updated,
                        "error": None,
                        "reason": None,
                    }
                else:
                    if committed.get("hints"):
                        hints_text = str(committed["hints"])[:500]
                        db.insert_cycle_annotation(cycle_id, "synthesis", "hints", hints_text)
                    db.complete_cycle_record(
                        cycle_id,
                        status="failed",
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        duration_ms=duration_api_ms,
                    )
                    return {
                        "status": "failed",
                        "backend": "claude",
                        "cost_usd": cost_usd,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "duration_ms": duration_api_ms,
                        "events_processed": pending_count,
                        "memex_updated": False,
                        "error": "Synthesis failed via commit_cycle",
                        "reason": None,
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
                cycle_id,
                status="incomplete",
                cost_usd=cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_api_ms,
            )
            return {
                "status": "failed",
                "backend": "claude",
                "cost_usd": cost_usd,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_ms": duration_api_ms,
                "events_processed": pending_count,
                "memex_updated": False,
                "error": "synthesis did not call commit_cycle",
                "reason": None,
            }

    except Exception as e:
        log.error("Synthesis failed for %s: %s", user_id, e)
        return {
            "status": "failed",
            "backend": "claude",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
            "events_processed": None,
            "memex_updated": False,
            "error": str(e),
            "reason": None,
        }


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
            _run_synthesis(
                db, user_id, observer=observer, run_id=run_id, skill_override=skill_override
            ),
            timeout=SYNC_TIMEOUT,
        )
    except TimeoutError:
        log.error("Synthesis timed out for %s after %ds", user_id, SYNC_TIMEOUT)
        return {
            "status": "failed",
            "backend": "claude",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
            "events_processed": None,
            "memex_updated": False,
            "error": f"Timed out after {SYNC_TIMEOUT}s",
            "reason": None,
        }


def synthesize(
    db: SykeDB, user_id: str, force: bool = False, skill_override: str | None = None
) -> dict[str, object]:
    result: dict[str, object]
    observer_api = importlib.import_module("syke.observe.trace")
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
                "events_processed": 0,
                "cost_usd": 0.0,
                "reason": "below_threshold",
            },
            run_id=run_id,
        )
        return {
            "status": "skipped",
            "backend": "claude",
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
            "events_processed": 0,
            "memex_updated": False,
            "error": None,
            "reason": "below_threshold",
        }

    try:
        result = asyncio.run(
            _run_synthesis_with_timeout(
                db, user_id, observer=observer, run_id=run_id, skill_override=skill_override
            )
        )
    except Exception as e:
        log.error("Synthesis error for %s: %s", user_id, e)
        result = {
            "status": "failed",
            "backend": "claude",
            "cost_usd": None,
            "input_tokens": None,
            "output_tokens": None,
            "duration_ms": None,
            "events_processed": None,
            "memex_updated": False,
            "error": str(e),
            "reason": None,
        }

    ended_at = datetime.now(UTC)
    observer.record(
        observer_api.SYNTHESIS_COMPLETE,
        {
            "start_time": started_at.isoformat(),
            "end_time": ended_at.isoformat(),
            "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
            "events_processed": result.get("events_processed", 0),
            "cost_usd": result.get("cost_usd", 0.0),
            "status": result.get("status", "unknown"),
            "error": result.get("error"),
        },
        run_id=run_id,
    )
    return cast(dict[str, object], result)
