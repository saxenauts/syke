"""Ask agent — synthesizes an answer from the user's timeline.

Same sandbox, same skill file, same DB access as synthesis.
The only difference is the task message.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
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
from syke.memory.synthesis import (
    _budget_scale_factor,
    _compute_real_cost,
    _load_skill_file,
    _resolve_proxy_model,
)
from syke.time import temporal_grounding_block

log = logging.getLogger(__name__)


class AskError(RuntimeError):
    """Raised when the ask agent fails."""


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
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_tok = usage.get("cache_read_input_tokens", 0)

        sdk_cost = result.total_cost_usd or 0.0
        proxy_model = _resolve_proxy_model()
        real_cost = _compute_real_cost(proxy_model, in_tok, out_tok, cache_tok) if proxy_model else None
        cost = real_cost if real_cost is not None else sdk_cost
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
        pass
    return summary


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
        agent_env = build_agent_env()
        memex_content = get_memex_for_injection(db, user_id)
        tg = temporal_grounding_block()
        skill_content, _ = _load_skill_file()
        db_file = str(db.db_path)

        # --- Same sandbox as synthesis ---
        sandbox_cwd = tempfile.mkdtemp(prefix="syke_sandbox_")
        sandbox_db = os.path.join(sandbox_cwd, "events.db")
        os.symlink(os.path.abspath(db_file), sandbox_db)
        db_file = sandbox_db

        # Same runtime context as synthesis
        runtime_context = (
            f"\n---\n\n## Runtime Context\n\n"
            f"### Current Document\n{memex_content or '[Empty]'}\n\n"
            f"### Data\n"
            f"Database: events.db\n"
            f'Query: sqlite3 {db_file} "YOUR SQL HERE"\n\n'
            f"Schema: id, timestamp, source, event_type, title, role, model, content, "
            f"stop_reason, input_tokens, output_tokens, session_id, sequence_index, "
            f"parent_event_id, external_id, ingested_at, user_id\n\n"
            f"{tg}\n"
        )
        prompt = skill_content + runtime_context

        # Scale budget for proxy model
        budget = ASK_BUDGET
        proxy_model = _resolve_proxy_model()
        if proxy_model:
            scale = _budget_scale_factor(proxy_model)
            if scale > 1:
                budget = budget * scale

        options = ClaudeAgentOptions(
            system_prompt=prompt,
            allowed_tools=["Bash", "Read", "Grep"],
            permission_mode="bypassPermissions",
            max_turns=ASK_MAX_TURNS,
            max_budget_usd=budget,
            model=ASK_MODEL,
            include_partial_messages=streaming,
            thinking={"type": "enabled", "budget_tokens": 10000},
            env=agent_env,
            cwd=sandbox_cwd,
        )

        task = f"Answer this question:\n\n{question}"
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
