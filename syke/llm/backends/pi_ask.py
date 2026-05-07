"""Pi-based ask implementation."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from uuid_extensions import uuid7

from syke.config import ASK_TIMEOUT
from syke.db import SykeDB
from syke.llm.backends import AskEvent
from syke.runtime import get_pi_runtime, start_pi_runtime

logger = logging.getLogger(__name__)


def _safe_runtime_status(runtime: object) -> dict[str, Any]:
    status_fn = getattr(runtime, "status", None)
    if callable(status_fn):
        try:
            status = status_fn()
            if isinstance(status, dict):
                return status
        except Exception:
            logger.debug("Failed to read Pi runtime status", exc_info=True)
    return {}


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
    num_turns: int | None = None,
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
        "num_turns": num_turns,
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


def _enrich_ask_metadata(
    metadata: dict[str, object],
    *,
    transport: str,
    transport_details: dict[str, object],
) -> dict[str, object]:
    if transport == "direct" and not transport_details:
        return metadata
    enriched = dict(metadata)
    enriched["transport"] = transport
    enriched.update(transport_details)
    return enriched


def _should_persist_trace(transport: str) -> bool:
    """Benchmark ask/judge runs must not write current-run traces into the
    same evidence surface they are evaluating."""
    return transport not in {"benchmark", "benchmark_judge"}


def _runtime_profile_for_transport(transport: str) -> str | None:
    if transport == "benchmark_judge":
        return "benchmark_judge"
    return None


def pi_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    **kwargs: object,
) -> tuple[str, dict[str, object]]:
    """Ask Pi a question using the workspace-backed runtime."""
    timeout_raw = kwargs.get("timeout", ASK_TIMEOUT)
    timeout = (
        float(timeout_raw)
        if isinstance(timeout_raw, (int, float)) and timeout_raw > 0
        else float(ASK_TIMEOUT)
    )
    on_event_raw = kwargs.get("on_event")
    transport_raw = kwargs.get("transport")
    model_raw = kwargs.get("model")
    capture_trace = bool(kwargs.get("capture_trace", False))
    model = model_raw if isinstance(model_raw, str) and model_raw else None
    transport = transport_raw if isinstance(transport_raw, str) and transport_raw else "direct"
    transport_details_raw = kwargs.get("transport_details")
    transport_details = (
        dict(transport_details_raw) if isinstance(transport_details_raw, dict) else {}
    )
    on_event: Callable[[AskEvent], None] | None = None
    if callable(on_event_raw):
        on_event = cast(Callable[[AskEvent], None], on_event_raw)
    started_at = datetime.now(UTC)
    run_id = None

    try:
        from syke.runtime import workspace as workspace_module

        if not workspace_module.WORKSPACE_ROOT.is_dir():
            return "Workspace not initialized. Run `syke setup`.", _canonical_ask_metadata(
                backend="pi"
            )

        run_id = str(uuid7())
        runtime_reused = False
        try:
            existing_runtime = get_pi_runtime()
            existing_status = _safe_runtime_status(existing_runtime)
            runtime_reused = bool(existing_runtime.is_alive) and existing_status.get(
                "workspace"
            ) == str(workspace_module.WORKSPACE_ROOT)
        except RuntimeError:
            pass

        from syke.source_selection import get_selected_sources

        runtime = start_pi_runtime(
            workspace_dir=workspace_module.WORKSPACE_ROOT,
            session_dir=workspace_module.SESSIONS_DIR,
            model=model,
            runtime_profile=_runtime_profile_for_transport(transport),
            selected_sources=get_selected_sources(user_id),
        )

        streamed_text = False

        def _on_raw_event(raw_event: dict[str, Any]) -> None:
            nonlocal streamed_text
            if _translate_pi_event(raw_event, on_event):
                streamed_text = True

        started = time.monotonic()

        def _persist_trace(
            *,
            status: str,
            error: str | None,
            output_text: str,
            thinking: list[str] | None,
            transcript: list[dict[str, Any]] | None,
            tool_calls: list[dict[str, Any]] | None,
            duration_ms: int,
            cost_usd: float | None,
            input_tokens: int | None,
            output_tokens: int | None,
            cache_read_tokens: int | None,
            cache_write_tokens: int | None,
            provider: str | None,
            model: str | None,
            response_id: str | None,
            stop_reason: str | None,
            runtime_reused: bool,
            runtime_status: dict[str, Any] | None,
        ) -> str | None:
            if run_id is None:
                return None
            try:
                from syke.trace_store import persist_rollout_trace

                _ = persist_rollout_trace(
                    db=db,
                    user_id=user_id,
                    run_id=run_id,
                    kind="ask",
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    status=status,
                    error=error,
                    input_text=question,
                    output_text=output_text,
                    thinking=thinking,
                    transcript=transcript,
                    tool_calls=tool_calls,
                    metrics={
                        "duration_ms": duration_ms,
                        "cost_usd": cost_usd,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "cache_write_tokens": cache_write_tokens,
                    },
                    runtime={
                        "provider": provider,
                        "model": model,
                        "response_id": response_id,
                        "stop_reason": stop_reason,
                        "num_turns": num_turns,
                        "runtime_reused": runtime_reused,
                        "runtime_pid": runtime_status.get("pid")
                        if isinstance(runtime_status, dict)
                        else None,
                        "runtime_uptime_s": runtime_status.get("uptime_s")
                        if isinstance(runtime_status, dict)
                        else None,
                        "runtime_session_count": runtime_status.get("session_count")
                        if isinstance(runtime_status, dict)
                        else None,
                        "transport": transport,
                        **transport_details,
                    },
                )
                return run_id
            except Exception:
                logger.debug("Failed to persist ask trace", exc_info=True)
                return None

        def _pause_db_connection_for_agent() -> bool:
            if not os.environ.get("SYKE_REPLAY_PAUSE_DB_CONNECTION_DURING_PI"):
                return False
            if getattr(db, "db_path", ":memory:") == ":memory:":
                return False
            try:
                db.conn.commit()
                db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.close()
                logger.info("Replay DB connection paused while Pi ask runs")
                return True
            except Exception:
                logger.warning("Failed to pause replay DB connection before Pi ask", exc_info=True)
                return False

        def _resume_db_connection_after_agent(paused: bool) -> None:
            if not paused:
                return
            db._conn = db._connect_db(db.db_path)  # type: ignore[attr-defined]
            db._in_transaction = False  # type: ignore[attr-defined]
            db.initialize()
            logger.info("Replay DB connection resumed after Pi ask run")

        try:
            db_paused_for_agent = _pause_db_connection_for_agent()
            try:
                result = runtime.prompt(
                    question,
                    timeout=timeout,
                    on_event=_on_raw_event if on_event else None,
                    new_session=True,
                )
            finally:
                _resume_db_connection_after_agent(db_paused_for_agent)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            runtime_status = _safe_runtime_status(runtime)
            logger.exception("Pi ask failed for user %s", user_id)
            error_text = f"Pi ask failed: {exc}"
            trace_id = (
                _persist_trace(
                    status="failed",
                    error=error_text,
                    output_text="",
                    thinking=None,
                    transcript=None,
                    tool_calls=None,
                    duration_ms=duration_ms,
                    cost_usd=None,
                    input_tokens=None,
                    output_tokens=None,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    provider=None,
                    model=None,
                    response_id=None,
                    stop_reason=None,
                    runtime_reused=runtime_reused,
                    runtime_status=runtime_status,
                )
                if _should_persist_trace(transport)
                else None
            )
            metadata_transport_details = (
                {**transport_details, "trace_id": trace_id}
                if trace_id is not None
                else transport_details
            )
            failed_metadata = _enrich_ask_metadata(
                _canonical_ask_metadata(
                    backend="pi",
                    duration_ms=duration_ms,
                    tool_calls=0,
                    num_turns=0,
                    error=error_text,
                ),
                transport=transport,
                transport_details=metadata_transport_details,
            )
            if capture_trace:
                failed_metadata["_input_text"] = question
                failed_metadata["_trace_payload"] = {
                    "status": "failed",
                    "error": error_text,
                    "output_text": "",
                    "thinking": [],
                    "transcript": [],
                    "tool_calls_detail": [],
                    "runtime": runtime_status if isinstance(runtime_status, dict) else {},
                }
            return (error_text, failed_metadata)

        duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
        runtime_status = _safe_runtime_status(runtime)
        num_turns = result.num_turns if isinstance(getattr(result, "num_turns", None), int) else 0
        metadata = _canonical_ask_metadata(
            backend="pi",
            cost_usd=result.cost_usd,
            duration_ms=duration_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            tool_calls=len(result.tool_calls),
            num_turns=num_turns,
            provider=result.provider,
            model=result.response_model,
            error=None,
        )
        trace_id = (
            _persist_trace(
                status="completed" if result.ok else "failed",
                error=None if result.ok else (result.error or "Pi ask failed"),
                output_text=result.output,
                thinking=getattr(result, "thinking", []) or [],
                transcript=getattr(result, "transcript", []) or [],
                tool_calls=result.tool_calls,
                duration_ms=duration_ms,
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                cache_write_tokens=result.cache_write_tokens,
                provider=result.provider,
                model=result.response_model,
                response_id=result.response_id,
                stop_reason=result.stop_reason,
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
            )
            if _should_persist_trace(transport)
            else None
        )
        metadata_transport_details = (
            {**transport_details, "trace_id": trace_id}
            if trace_id is not None
            else transport_details
        )
        if capture_trace:
            metadata["_input_text"] = question
            metadata["_trace_payload"] = {
                "status": "completed" if result.ok else "failed",
                "error": None if result.ok else (result.error or "Pi ask failed"),
                "output_text": result.output,
                "thinking": getattr(result, "thinking", []) or [],
                "transcript": getattr(result, "transcript", []) or [],
                "tool_calls_detail": result.tool_calls,
                "runtime": {
                    "provider": result.provider,
                    "model": result.response_model,
                    "response_id": result.response_id,
                    "stop_reason": result.stop_reason,
                    "num_turns": num_turns,
                    "runtime_reused": runtime_reused,
                    "runtime_status": runtime_status if isinstance(runtime_status, dict) else {},
                },
            }

        if result.ok:
            if on_event is not None and not streamed_text and result.output:
                on_event(AskEvent(type="text", content=result.output))
            return result.output, _enrich_ask_metadata(
                metadata,
                transport=transport,
                transport_details=metadata_transport_details,
            )

        error_message = result.error or "Pi ask failed"
        metadata["error"] = error_message
        return error_message, _enrich_ask_metadata(
            metadata,
            transport=transport,
            transport_details=metadata_transport_details,
        )
    finally:
        pass
