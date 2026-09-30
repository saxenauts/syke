"""Pi-based ask implementation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from uuid_extensions import uuid7

from syke.config import ASK_TIMEOUT
from syke.db import SykeDB
from syke.llm.backends import AskEvent
from syke.llm.pi_client import pi_bash_spill_path_from_event, remove_pi_bash_spills
from syke.source_selection import get_selected_sources

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
    num_turns: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    session_file: str | None = None,
    session_name: str | None = None,
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
        "session_id": session_id,
        "session_file": session_file,
        "session_name": session_name,
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
        inner = raw_event.get("assistantMessageEvent")
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
        return emitted_text

    if event_type == "tool_execution_start":
        on_event(
            AskEvent(
                type="tool_call",
                content=str(raw_event.get("toolName") or "tool"),
                metadata={"input": raw_event.get("args")},
            )
        )

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


def pi_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    **kwargs: object,
) -> tuple[str, dict[str, object]]:
    """Run one foreground Syke attention episode."""
    timeout_raw = kwargs.get("timeout", ASK_TIMEOUT)
    timeout = (
        float(timeout_raw)
        if isinstance(timeout_raw, (int, float)) and timeout_raw > 0
        else float(ASK_TIMEOUT)
    )
    on_event_raw = kwargs.get("on_event")
    transport_raw = kwargs.get("transport")
    model_raw = kwargs.get("model")
    model = model_raw if isinstance(model_raw, str) and model_raw else None
    transport = transport_raw if isinstance(transport_raw, str) and transport_raw else "direct"
    transport_details_raw = kwargs.get("transport_details")
    transport_details = (
        dict(transport_details_raw) if isinstance(transport_details_raw, dict) else {}
    )
    on_event: Callable[[AskEvent], None] | None = None
    if callable(on_event_raw):
        on_event = cast(Callable[[AskEvent], None], on_event_raw)

    from syke.runtime import start_pi_runtime
    from syke.runtime import workspace as workspace_module
    from syke.runtime.prompt_context import build_prompt, format_now_for_prompt

    workspace_root = workspace_module.WORKSPACE_ROOT
    session_dir = workspace_module.SESSIONS_DIR
    started = time.monotonic()
    run_id = str(uuid7())
    operation_runtime = session_dir.parent / "runtime" / "cycles" / run_id
    runtime_tmp = session_dir.parent / "runtime" / "tmp"
    pi_bash_spills: set[Path] = set()

    if not workspace_root.is_dir():
        error_text = "Workspace not initialized. Run `syke setup`."
        return error_text, _enrich_ask_metadata(
            _canonical_ask_metadata(
                backend="pi",
                duration_ms=int((time.monotonic() - started) * 1000),
                tool_calls=0,
                num_turns=0,
                error=error_text,
            ),
            transport=transport,
            transport_details=transport_details,
        )

    selected_sources = get_selected_sources(user_id)
    streamed_text = False
    external_on_event = on_event

    def _on_raw_event(raw_event: dict[str, Any]) -> None:
        nonlocal external_on_event, streamed_text
        spill = pi_bash_spill_path_from_event(raw_event, runtime_tmp)
        if spill is not None:
            pi_bash_spills.add(spill)
        try:
            if _translate_pi_event(raw_event, external_on_event):
                streamed_text = True
        except Exception:
            logger.debug("Ask event callback failed", exc_info=True)
            external_on_event = None

    try:
        operation_runtime.mkdir(parents=True, exist_ok=False)
        prompt = build_prompt(
            workspace_root,
            db=db,
            user_id=user_id,
            context="ask",
            now=format_now_for_prompt(datetime.now()),
            selected_sources=selected_sources,
            session_dir=session_dir,
            cycle_runtime=operation_runtime,
            operation_id=run_id,
            condition="ordinary",
            answer_obligation=question,
            time_limit_s=timeout,
        )
        runtime = start_pi_runtime(
            workspace_dir=workspace_root,
            session_dir=session_dir,
            model=model,
        )

        result = runtime.prompt(
            prompt,
            timeout=timeout,
            on_event=_on_raw_event,
            new_session=True,
            session_name=f"syke:ask:{run_id}",
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception("Pi ask failed for user %s", user_id)
        error_text = f"Pi ask failed: {exc}"
        return (
            error_text,
            _enrich_ask_metadata(
                _canonical_ask_metadata(
                    backend="pi",
                    duration_ms=duration_ms,
                    tool_calls=0,
                    num_turns=0,
                    error=error_text,
                ),
                transport=transport,
                transport_details=transport_details,
            ),
        )
    finally:
        remove_pi_bash_spills(pi_bash_spills)
        try:
            operation_runtime.rmdir()
        except OSError:
            pass

    duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
    num_turns = result.num_turns if isinstance(result.num_turns, int) else 0
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
        session_id=result.session_id,
        session_file=result.session_file,
        session_name=result.session_name,
        error=None,
    )

    if result.ok:
        if external_on_event is not None and not streamed_text and result.output:
            external_on_event(AskEvent(type="text", content=result.output))
        return result.output, _enrich_ask_metadata(
            metadata,
            transport=transport,
            transport_details=transport_details,
        )

    error_message = result.error or "Pi ask failed"
    metadata["error"] = error_message
    return error_message, _enrich_ask_metadata(
        metadata,
        transport=transport,
        transport_details=transport_details,
    )
