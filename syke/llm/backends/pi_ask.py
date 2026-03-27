"""Pi-based ask implementation."""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from syke.db import SykeDB
from syke.llm.backends import AskEvent
from syke.runtime import get_pi_runtime, start_pi_runtime
from syke.runtime.workspace import MEMEX_PATH, SESSIONS_DIR, WORKSPACE_ROOT, prepare_workspace
from uuid_extensions import uuid7

logger = logging.getLogger(__name__)


def _summarize_tools(tool_calls: list[dict[str, Any]]) -> tuple[list[str], dict[str, int]]:
    names: list[str] = []
    counts: dict[str, int] = {}
    for tool_call in tool_calls:
        name = tool_call.get("name") or tool_call.get("tool") or "tool"
        name_str = str(name)
        names.append(name_str)
        counts[name_str] = counts.get(name_str, 0) + 1
    return names, counts


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
    response_id: str | None,
    stop_reason: str | None,
    status: str,
    runtime_reused: bool,
    runtime_status: dict[str, Any] | None,
    workspace_refresh: dict[str, object] | None,
    tool_names: list[str],
    tool_name_counts: dict[str, int],
    transport: str,
    transport_details: dict[str, object] | None,
) -> None:
    try:
        from syke.metrics import MetricsTracker, RunMetrics

        tracker = MetricsTracker(user_id)
        runtime_status = runtime_status or {}
        workspace_refresh = workspace_refresh or {}
        transport_details = transport_details or {}
        completed_at = datetime.now(UTC)
        started_at = completed_at - timedelta(milliseconds=max(duration_ms, 0))
        tracker.record(
            RunMetrics(
                operation="ask",
                user_id=user_id,
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                duration_seconds=duration_ms / 1000.0,
                duration_api_ms=duration_ms,
                cost_usd=float(cost_usd or 0.0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                details={
                    "tool_calls": tool_calls,
                    "tool_names": tool_names,
                    "tool_name_counts": tool_name_counts,
                    "status": status,
                    "provider": provider,
                    "model": model,
                    "response_id": response_id,
                    "stop_reason": stop_reason,
                    "runtime_reused": runtime_reused,
                    "runtime_pid": runtime_status.get("pid"),
                    "runtime_uptime_s": runtime_status.get("uptime_s"),
                    "runtime_start_ms": runtime_status.get("last_start_ms"),
                    "runtime_session_count": runtime_status.get("session_count"),
                    "cache_read_tokens": int(cache_read_tokens or 0),
                    "cache_write_tokens": int(cache_write_tokens or 0),
                    "workspace_refreshed": bool(workspace_refresh.get("refreshed", False)),
                    "workspace_refresh_reason": workspace_refresh.get("reason"),
                    "workspace_refresh_ms": workspace_refresh.get("duration_ms"),
                    "workspace_events_db_size": workspace_refresh.get("dest_size_bytes"),
                    "transport": transport,
                    **transport_details,
                },
            )
        )
    except Exception:
        logger.debug("Failed to record Pi ask metrics", exc_info=True)


def _record_ask_tool_observations(
    observer: object,
    run_id: str,
    tool_calls: list[dict[str, Any]],
) -> None:
    from syke.observe.trace import ASK_TOOL_USE

    if observer is None:
        return

    for index, tool_call in enumerate(tool_calls, start=1):
        try:
            tool_name = tool_call.get("name") or tool_call.get("tool") or "tool"
            observer.record(
                ASK_TOOL_USE,
                {
                    "tool_name": str(tool_name),
                    "tool_input": tool_call.get("input"),
                    "tool_index": index,
                    "success": True,
                },
                run_id=run_id,
            )
        except Exception:
            logger.debug("Failed to record Pi ask tool observation", exc_info=True)


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
    transport_raw = kwargs.get("transport")
    transport = transport_raw if isinstance(transport_raw, str) and transport_raw else "direct"
    transport_details_raw = kwargs.get("transport_details")
    transport_details = (
        dict(transport_details_raw) if isinstance(transport_details_raw, dict) else {}
    )
    on_event: Callable[[AskEvent], None] | None = None
    if callable(on_event_raw):
        on_event = cast(Callable[[AskEvent], None], on_event_raw)
    started_at = datetime.now(UTC)
    observer_api = None
    observer = None
    run_id = None

    try:
        source_db = Path(db.db_path) if hasattr(db, "db_path") else None
        workspace_info = prepare_workspace(user_id, source_db_path=source_db)
        workspace_refresh = cast(
            dict[str, object],
            workspace_info.get("refresh", {}),
        )

        from syke.memory.memex import get_memex_for_injection

        memex_text = get_memex_for_injection(db, user_id)
        try:
            MEMEX_PATH.write_text(memex_text + ("\n" if memex_text else ""), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to sync memex context into %s: %s", MEMEX_PATH, exc)

        if db.count_events(user_id) == 0 and memex_text.strip() == "[No data yet.]":
            no_data = "No data yet. Run `syke sync` or wait for ingestion first."
            return no_data, _canonical_ask_metadata(backend="pi")

        observer_api = importlib.import_module("syke.observe.trace")
        observer = observer_api.SykeObserver(db, user_id)
        run_id = str(uuid7())
        observer.record(
            observer_api.ASK_START,
            {
                "start_time": started_at.isoformat(),
                "question_preview": question[:200],
                "transport": transport,
                **transport_details,
            },
            run_id=run_id,
        )

        def _record_completion(
            *,
            status: str,
            error: str | None,
            duration_ms: int,
            cost_usd: float | None,
            input_tokens: int | None,
            output_tokens: int | None,
            cache_read_tokens: int | None,
            cache_write_tokens: int | None,
            tool_calls: list[dict[str, Any]],
            provider: str | None,
            model: str | None,
            response_id: str | None,
            stop_reason: str | None,
            runtime_reused: bool | None,
            runtime_status: dict[str, Any] | None,
            workspace_refresh: dict[str, object] | None,
        ) -> None:
            ended_at = datetime.now(UTC)
            tool_names, tool_name_counts = _summarize_tools(tool_calls)
            observer.record(
                observer_api.ASK_COMPLETE,
                {
                    "start_time": started_at.isoformat(),
                    "end_time": ended_at.isoformat(),
                    "duration_ms": duration_ms,
                    "status": status,
                    "error": error,
                    "cost_usd": cost_usd,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": int(cache_read_tokens or 0),
                    "cache_write_tokens": int(cache_write_tokens or 0),
                    "provider": provider,
                    "model": model,
                    "response_id": response_id,
                    "stop_reason": stop_reason,
                    "tool_calls": len(tool_calls),
                    "tool_names": tool_names,
                    "tool_name_counts": tool_name_counts,
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
                    "workspace_refreshed": bool(workspace_refresh.get("refreshed", False))
                    if isinstance(workspace_refresh, dict)
                    else False,
                    "workspace_refresh_reason": workspace_refresh.get("reason")
                    if isinstance(workspace_refresh, dict)
                    else None,
                    "workspace_refresh_ms": workspace_refresh.get("duration_ms")
                    if isinstance(workspace_refresh, dict)
                    else None,
                    "workspace_events_db_size": workspace_refresh.get("dest_size_bytes")
                    if isinstance(workspace_refresh, dict)
                    else None,
                    "transport": transport,
                    **transport_details,
                },
                run_id=run_id,
            )
            _record_ask_tool_observations(observer, run_id, tool_calls)

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

        runtime_reused = False
        try:
            runtime = get_pi_runtime()
            runtime_reused = runtime.is_alive
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
            runtime.new_session()
            result = runtime.prompt(prompt, timeout=timeout, on_event=_on_raw_event if on_event else None)
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            runtime_status = _safe_runtime_status(runtime)
            logger.exception("Pi ask failed for user %s", user_id)
            error_text = f"Pi ask failed: {exc}"
            _record_ask_metrics(
                user_id,
                duration_ms=duration_ms,
                cost_usd=None,
                input_tokens=None,
                output_tokens=None,
                cache_read_tokens=None,
                cache_write_tokens=None,
                tool_calls=0,
                provider=None,
                model=None,
                response_id=None,
                stop_reason=None,
                status="failed",
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                workspace_refresh=workspace_refresh,
                tool_names=[],
                tool_name_counts={},
                transport=transport,
                transport_details=transport_details,
            )
            _record_completion(
                status="failed",
                error=error_text,
                duration_ms=duration_ms,
                cost_usd=None,
                input_tokens=None,
                output_tokens=None,
                cache_read_tokens=None,
                cache_write_tokens=None,
                tool_calls=[],
                provider=None,
                model=None,
                response_id=None,
                stop_reason=None,
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                workspace_refresh=workspace_refresh,
            )
            return (
                error_text,
                _enrich_ask_metadata(
                    _canonical_ask_metadata(
                        backend="pi",
                        duration_ms=duration_ms,
                        tool_calls=0,
                        error=error_text,
                    ),
                    transport=transport,
                    transport_details=transport_details,
                ),
            )

        duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
        runtime_status = _safe_runtime_status(runtime)
        tool_names, tool_name_counts = _summarize_tools(result.tool_calls)
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
                response_id=result.response_id,
                stop_reason=result.stop_reason,
                status="completed",
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                workspace_refresh=workspace_refresh,
                tool_names=tool_names,
                tool_name_counts=tool_name_counts,
                transport=transport,
                transport_details=transport_details,
            )
            _record_completion(
                status="completed",
                error=None,
                duration_ms=duration_ms,
                cost_usd=result.cost_usd,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_tokens=result.cache_read_tokens,
                cache_write_tokens=result.cache_write_tokens,
                tool_calls=result.tool_calls,
                provider=result.provider,
                model=result.response_model,
                response_id=result.response_id,
                stop_reason=result.stop_reason,
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                workspace_refresh=workspace_refresh,
            )
            if on_event is not None and not streamed_text and result.output:
                on_event(AskEvent(type="text", content=result.output))
            return result.output, _enrich_ask_metadata(
                metadata,
                transport=transport,
                transport_details=transport_details,
            )

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
            response_id=result.response_id,
            stop_reason=result.stop_reason,
            status="failed",
            runtime_reused=runtime_reused,
            runtime_status=runtime_status,
            workspace_refresh=workspace_refresh,
            tool_names=tool_names,
            tool_name_counts=tool_name_counts,
            transport=transport,
            transport_details=transport_details,
        )
        _record_completion(
            status="failed",
            error=error_message,
            duration_ms=duration_ms,
            cost_usd=result.cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_write_tokens=result.cache_write_tokens,
            tool_calls=result.tool_calls,
            provider=result.provider,
            model=result.response_model,
            response_id=result.response_id,
            stop_reason=result.stop_reason,
            runtime_reused=runtime_reused,
            runtime_status=runtime_status,
            workspace_refresh=workspace_refresh,
        )
        return error_message, _enrich_ask_metadata(
            _canonical_ask_metadata(
                backend="pi",
                cost_usd=result.cost_usd,
                duration_ms=duration_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                tool_calls=len(result.tool_calls),
                error=error_message,
            ),
            transport=transport,
            transport_details=transport_details,
        )
    finally:
        if observer is not None:
            observer.close()
