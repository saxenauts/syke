"""Pi-native ask and synthesis dispatch."""

from __future__ import annotations

import logging
from collections.abc import Callable
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

    # Inject full context: PSYCHE (identity) + NOW (time) + MEMEX (map) + skill (reasoning).
    # The agent starts with everything — no file reads needed for basic context.
    from datetime import datetime

    from syke.runtime.psyche_md import build_prompt, format_now_for_prompt
    from syke.runtime.workspace import WORKSPACE_ROOT
    from syke.source_selection import get_selected_sources

    base = build_prompt(
        WORKSPACE_ROOT,
        db=db,
        user_id=user_id,
        now=format_now_for_prompt(datetime.now()),
        context="ask",
        include_synthesis=False,
        selected_sources=get_selected_sources(user_id),
    )
    question = f"{base}\n---\n\nUser question: {question}"

    db_path = getattr(db, "db_path", None)
    timeout = _resolve_ask_timeout(kwargs.get("timeout"))
    if timeout is not None:
        kwargs["timeout"] = timeout
    else:
        kwargs.pop("timeout", None)
    if isinstance(db_path, str) and db_path and db_path != ":memory:":
        from syke.daemon.ipc import (
            ask_via_daemon,
        )

        return ask_via_daemon(
            user_id=user_id,
            syke_db_path=db_path,
            question=question,
            on_event=kwargs.get("on_event"),
            timeout=timeout,
        )

    from syke.llm.backends.pi_ask import pi_ask

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
