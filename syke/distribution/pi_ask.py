"""Pi-based ask — lightweight alternative to the Claude Agent SDK ask path.

Uses PiClient (sync subprocess RPC) instead of the async ClaudeSDKClient.
Two-turn conversation: first turn loads the memex as system context,
second turn poses the user's question.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Callable
from typing import Any

from syke.config import DEFAULT_USER, user_db_path
from syke.config_file import load_config
from syke.db import SykeDB
from syke.llm.pi_client import PiClient, resolve_pi_model
from syke.memory.memex import get_memex_for_injection

log = logging.getLogger(__name__)


def ask(
    question: str,
    on_event: Callable[[Any], None] | None = None,
) -> tuple[str, dict[str, float]]:
    """Ask a question using Pi as the backend.

    Resolves config, user, model, and memex internally.
    Returns ``(answer_text, cost_dict)`` matching the shape of
    :func:`syke.distribution.ask_agent.ask`.
    """
    config = load_config()
    user_id = config.user or DEFAULT_USER
    model = resolve_pi_model(config)

    db = SykeDB(user_db_path(user_id))
    memex = get_memex_for_injection(db, user_id)

    system_msg = (
        "You are Syke, a personal memory assistant. "
        "Use this memex context:\n\n" + memex
    )

    wall_start = _time.monotonic()

    with PiClient(model=model) as pi:
        # Turn 1: set context with memex
        pi.prompt(system_msg)

        # Turn 2: ask the actual question
        result = pi.prompt(question)

        # Grab session stats for cost tracking
        stats = pi.command("get_session_stats")

    wall_seconds = _time.monotonic() - wall_start

    # Extract cost information from stats response
    cost_dict: dict[str, float] = {"duration_seconds": wall_seconds}

    if stats.get("success"):
        data = stats.get("data", stats)
        cost_dict["cost_usd"] = float(data.get("cost_usd", 0.0))
        cost_dict["tokens"] = float(
            data.get("total_tokens", 0)
            or (data.get("input_tokens", 0) + data.get("output_tokens", 0))
        )
    else:
        # Stats unavailable — fill from the prompt result usage if present
        usage = result.get("usage", {})
        cost_dict["cost_usd"] = 0.0
        cost_dict["tokens"] = float(
            usage.get("total_tokens", 0)
            or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        )

    return (result["output"], cost_dict)
