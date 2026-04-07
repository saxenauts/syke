"""Pi-native ask and synthesis dispatch."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from syke.config import ASK_TIMEOUT
from syke.db import SykeDB
from syke.llm.backends import AskEvent

logger = logging.getLogger(__name__)


def _resolve_ask_timeout(timeout_raw: object) -> float | None:
    if isinstance(timeout_raw, (int, float)) and timeout_raw > 0:
        return float(timeout_raw)
    if ASK_TIMEOUT > 0:
        return float(ASK_TIMEOUT)
    return None


def run_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    **kwargs: Any,
) -> tuple[str, dict[str, object]]:
    logger.info("Routing ask to Pi runtime")

    # Inject PSYCHE identity preamble before the user's question.
    # This ensures the agent always knows it is Syke, regardless of
    # whether the ask goes through daemon IPC or direct Pi.
    from syke.runtime.psyche_md import _build_psyche_md
    from syke.runtime.workspace import WORKSPACE_ROOT

    psyche_content = _build_psyche_md(WORKSPACE_ROOT)
    question = f"{psyche_content}\n---\n\nUser question: {question}"

    db_path = getattr(db, "db_path", None)
    event_db_path = getattr(db, "event_db_path", None)
    timeout = _resolve_ask_timeout(kwargs.get("timeout"))
    if timeout is not None:
        kwargs["timeout"] = timeout
    else:
        kwargs.pop("timeout", None)
    daemon_attempt_error: str | None = None
    daemon_attempt_ms: int | None = None
    bypass_reason: str | None = None

    if (
        isinstance(db_path, str)
        and db_path
        and db_path != ":memory:"
        and Path(db_path).exists()
        and isinstance(event_db_path, str)
        and event_db_path
    ):
        from syke.daemon.ipc import (
            DaemonIpcUnavailable,
            ask_via_daemon,
            daemon_runtime_status,
        )

        daemon_status = daemon_runtime_status(user_id, timeout=0.25)
        if daemon_status.get("alive") and daemon_status.get("busy"):
            bypass_reason = "daemon_busy"
            logger.info("Daemon runtime busy for ask; bypassing IPC and using direct Pi ask")
        else:
            daemon_started = time.monotonic()
            try:
                return ask_via_daemon(
                    user_id=user_id,
                    syke_db_path=db_path,
                    event_db_path=event_db_path,
                    question=question,
                    on_event=kwargs.get("on_event"),
                    timeout=timeout,
                )
            except DaemonIpcUnavailable as exc:
                daemon_attempt_error = str(exc)
                daemon_attempt_ms = int((time.monotonic() - daemon_started) * 1000)
                logger.info(
                    "Daemon IPC unavailable for ask; falling back to direct Pi ask: %s",
                    exc,
                )
            except Exception as exc:
                daemon_attempt_error = str(exc) or "daemon IPC ask failed"
                daemon_attempt_ms = int((time.monotonic() - daemon_started) * 1000)
                logger.warning(
                    "Daemon IPC ask failed; falling back to direct Pi ask",
                    exc_info=True,
                )

    from syke.llm.backends.pi_ask import pi_ask

    if daemon_attempt_error is not None or bypass_reason is not None:
        transport_details = kwargs.get("transport_details")
        merged_transport_details = (
            dict(transport_details) if isinstance(transport_details, dict) else {}
        )
        if daemon_attempt_error is not None:
            merged_transport_details.update(
                {
                    "ipc_fallback": True,
                    "ipc_error": daemon_attempt_error,
                }
            )
        if daemon_attempt_ms is not None:
            merged_transport_details["ipc_attempt_ms"] = daemon_attempt_ms
        if bypass_reason is not None:
            merged_transport_details.update(
                {
                    "ipc_bypassed": True,
                    "ipc_bypass_reason": bypass_reason,
                }
            )
        kwargs["transport_details"] = merged_transport_details

    return pi_ask(db, user_id, question, **kwargs)


def run_ask_stream(
    db: SykeDB,
    user_id: str,
    question: str,
    on_event: Callable[[AskEvent], None] | None = None,
    **kwargs: Any,
) -> tuple[str, dict[str, object]]:
    if on_event is not None:
        kwargs["on_event"] = on_event
    return run_ask(db=db, user_id=user_id, question=question, **kwargs)
