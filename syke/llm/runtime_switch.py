"""Pi runtime entrypoints.

Syke now routes ask and synthesis exclusively through the Pi runtime. The old
runtime switch module remains as a stable import path for callers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from syke.db import SykeDB
from syke.llm.backends import AskEvent

logger = logging.getLogger(__name__)


def get_runtime() -> str:
    """Return the canonical Syke runtime backend."""
    return "pi"


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
