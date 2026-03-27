"""Pi-based ask implementation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from syke.db import SykeDB
from syke.llm.backends import AskEvent
from syke.runtime import get_pi_runtime, start_pi_runtime
from syke.runtime.workspace import MEMEX_PATH, SESSIONS_DIR, WORKSPACE_ROOT, setup_workspace

logger = logging.getLogger(__name__)


def _canonical_ask_metadata(
    *,
    backend: str = "pi",
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    tool_calls: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "backend": backend,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "tool_calls": tool_calls,
        "provider": provider,
        "model": model,
        "error": error,
    }


def _translate_pi_event(
    raw_event: dict[str, Any],
    on_event: Callable[[AskEvent], None] | None,
) -> bool:
    """Translate Pi RPC events into Syke AskEvent callbacks.

    Returns True if the callback emitted user-visible text content.
    """
    if on_event is None:
        return False

    emitted_text = False
    event_type = raw_event.get("type")

    if event_type == "message_update":
        inner = raw_event.get("assistantMessageEvent") or raw_event.get("event")
        if not isinstance(inner, dict):
            return False
        inner_type = inner.get("type")
        if inner_type == "thinking_delta":
            delta = inner.get("delta")
            if isinstance(delta, str) and delta:
                on_event(AskEvent(type="thinking", content=delta))
        elif inner_type == "text_delta":
            delta = inner.get("delta")
            if isinstance(delta, str) and delta:
                emitted_text = True
                on_event(AskEvent(type="text", content=delta))
        elif inner_type in {"toolcall_start", "toolcall_end"}:
            tool_call = inner.get("toolCall")
            if isinstance(tool_call, dict):
                name = tool_call.get("toolName") or tool_call.get("name") or "tool"
                on_event(
                    AskEvent(
                        type="tool_call",
                        content=str(name),
                        metadata={"input": tool_call.get("input")},
                    )
                )
        return emitted_text

    if event_type == "tool_execution_start":
        tool = raw_event.get("toolExecution")
        if isinstance(tool, dict):
            name = tool.get("name") or "tool"
            on_event(
                AskEvent(
                    type="tool_call",
                    content=str(name),
                    metadata={"input": tool.get("input")},
                )
            )
        return False

    return False


def _record_ask_metrics(
    user_id: str,
    *,
    duration_ms: int,
    cost_usd: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    tool_calls: int,
    provider: str | None,
    model: str | None,
    status: str,
) -> None:
    try:
        from syke.metrics import MetricsTracker, RunMetrics

        tracker = MetricsTracker(user_id)
        tracker.record(
            RunMetrics(
                operation="ask",
                user_id=user_id,
                duration_seconds=duration_ms / 1000.0,
                duration_api_ms=duration_ms,
                cost_usd=float(cost_usd or 0.0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                details={
                    "tool_calls": tool_calls,
                    "status": status,
                    "provider": provider,
                    "model": model,
                    "cache_read_tokens": int(cache_read_tokens or 0),
                    "cache_write_tokens": int(cache_write_tokens or 0),
                },
            )
        )
    except Exception:
        logger.debug("Failed to record Pi ask metrics", exc_info=True)


def pi_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    **kwargs: object,
) -> tuple[str, dict[str, object]]:
    """Ask Pi a question using the workspace-backed runtime."""
    timeout_raw = kwargs.get("timeout", 120)
    timeout = timeout_raw if isinstance(timeout_raw, (int, float)) else 120
    on_event_raw = kwargs.get("on_event")
    on_event: Callable[[AskEvent], None] | None = None
    if callable(on_event_raw):
        on_event = cast(Callable[[AskEvent], None], on_event_raw)

    source_db = Path(db.db_path) if hasattr(db, "db_path") else None
    setup_workspace(user_id, source_db_path=source_db)

    from syke.memory.memex import get_memex_for_injection

    memex_text = get_memex_for_injection(db, user_id)
    try:
        MEMEX_PATH.write_text(memex_text + ("\n" if memex_text else ""), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to sync memex context into %s: %s", MEMEX_PATH, exc)

    if db.count_events(user_id) == 0 and memex_text.strip() == "[No data yet.]":
        no_data = "No data yet. Run `syke sync` or wait for ingestion first."
        return no_data, _canonical_ask_metadata(backend="pi")

    memex_snippet = memex_text[:2000]

    prompt = (
        "You are answering a question inside the Syke Pi runtime.\n\n"
        "Use the workspace sources below when forming your answer:\n"
        f"- events.db at {WORKSPACE_ROOT / 'events.db'} (read-only timeline)\n"
        f"- agent.db at {WORKSPACE_ROOT / 'agent.db'} (workspace memory store)\n"
        f"- memex.md at {MEMEX_PATH}\n"
        f"- scripts/ at {WORKSPACE_ROOT / 'scripts'}\n\n"
        "Treat events.db as read-only. Prefer grounded answers based on these local sources. "
        "If needed, inspect scripts/ for helper tooling or analysis utilities.\n\n"
        f"User ID: {user_id}\n"
        f"Question: {question}\n"
    )

    if memex_snippet:
        prompt += f"\nMemex context snippet (first 2000 chars):\n---\n{memex_snippet}\n---\n"
    else:
        prompt += "\nMemex context snippet: memex.md not found or could not be read.\n"

    try:
        runtime = get_pi_runtime()
    except RuntimeError:
        logger.info("Pi runtime not initialized; starting a workspace-backed runtime for pi_ask")
        runtime = start_pi_runtime(
            workspace_dir=WORKSPACE_ROOT,
            session_dir=SESSIONS_DIR,
        )

    streamed_text = False

    def _on_raw_event(raw_event: dict[str, Any]) -> None:
        nonlocal streamed_text
        if _translate_pi_event(raw_event, on_event):
            streamed_text = True

    started = time.monotonic()
    try:
        result = runtime.prompt(prompt, timeout=timeout, on_event=_on_raw_event if on_event else None)
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception("Pi ask failed for user %s", user_id)
        error_text = f"Pi ask failed: {exc}"
        return (
            error_text,
            _canonical_ask_metadata(
                backend="pi",
                duration_ms=duration_ms,
                tool_calls=0,
                error=error_text,
            ),
        )

    duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
    metadata = _canonical_ask_metadata(
        backend="pi",
        cost_usd=result.cost_usd,
        duration_ms=duration_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_write_tokens=result.cache_write_tokens,
        tool_calls=len(result.tool_calls),
        provider=result.provider,
        model=result.response_model,
        error=None,
    )

    if result.ok:
        _record_ask_metrics(
            user_id,
            duration_ms=duration_ms,
            cost_usd=result.cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            tool_calls=len(result.tool_calls),
            provider=result.provider,
            model=result.response_model,
            status="completed",
        )
        if on_event is not None and not streamed_text and result.output:
            on_event(AskEvent(type="text", content=result.output))
        return result.output, metadata

    error_message = result.error or "Pi ask failed"
    _record_ask_metrics(
        user_id,
        duration_ms=duration_ms,
        cost_usd=result.cost_usd,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_write_tokens=result.cache_write_tokens,
        tool_calls=len(result.tool_calls),
        provider=result.provider,
        model=result.response_model,
        status="failed",
    )
    return error_message, _canonical_ask_metadata(
        backend="pi",
        cost_usd=result.cost_usd,
        duration_ms=duration_ms,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        tool_calls=len(result.tool_calls),
        error=error_message,
    )
