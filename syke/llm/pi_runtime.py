"""Pi-native ask and synthesis dispatch."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.llm.backends import AskEvent

logger = logging.getLogger(__name__)


def run_synthesis(db: SykeDB, user_id: str, **kwargs: Any) -> dict[str, object]:
    logger.info("Routing synthesis to Pi runtime")
    from syke.llm.backends.pi_synthesis import pi_synthesize

    return pi_synthesize(db, user_id, **kwargs)


def run_ask(
    db: SykeDB,
    user_id: str,
    question: str,
    **kwargs: Any,
) -> tuple[str, dict[str, object]]:
    logger.info("Routing ask to Pi runtime")
    db_path = getattr(db, "db_path", None)
    event_db_path = getattr(db, "event_db_path", None)
    timeout = kwargs.get("timeout")
    daemon_attempt_error: str | None = None
    daemon_attempt_ms: int | None = None

    if (
        isinstance(db_path, str)
        and db_path
        and db_path != ":memory:"
        and Path(db_path).exists()
        and isinstance(event_db_path, str)
        and event_db_path
    ):
        from syke.daemon.ipc import DaemonIpcUnavailable, ask_via_daemon

        daemon_started = time.monotonic()
        try:
            return ask_via_daemon(
                user_id=user_id,
                syke_db_path=db_path,
                event_db_path=event_db_path,
                question=question,
                on_event=kwargs.get("on_event"),
                timeout=timeout if isinstance(timeout, int | float) else None,
            )
        except DaemonIpcUnavailable as exc:
            daemon_attempt_error = str(exc)
            daemon_attempt_ms = int((time.monotonic() - daemon_started) * 1000)
            logger.info("Daemon IPC unavailable for ask; falling back to direct Pi ask: %s", exc)
        except Exception as exc:
            daemon_attempt_error = str(exc) or "daemon IPC ask failed"
            daemon_attempt_ms = int((time.monotonic() - daemon_started) * 1000)
            logger.warning("Daemon IPC ask failed; falling back to direct Pi ask", exc_info=True)

    from syke.llm.backends.pi_ask import pi_ask

    if daemon_attempt_error is not None:
        transport_details = kwargs.get("transport_details")
        merged_transport_details = (
            dict(transport_details) if isinstance(transport_details, dict) else {}
        )
        merged_transport_details.update(
            {
                "ipc_fallback": True,
                "ipc_error": daemon_attempt_error,
            }
        )
        if daemon_attempt_ms is not None:
            merged_transport_details["ipc_attempt_ms"] = daemon_attempt_ms
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
