"""Pi-based ask implementation."""

from __future__ import annotations

import logging
import time

from syke.db import SykeDB
from syke.runtime import get_pi_runtime, start_pi_runtime
from syke.runtime.workspace import MEMEX_PATH, SESSIONS_DIR, WORKSPACE_ROOT, setup_workspace

logger = logging.getLogger(__name__)


def pi_ask(db: SykeDB, user_id: str, question: str, **kwargs) -> tuple[str, dict]:
    """Ask Pi a question using the workspace-backed runtime."""
    del db  # Pi reads from the workspace databases directly.

    timeout = kwargs.get("timeout", 120)
    memex_snippet = ""

    if MEMEX_PATH.exists():
        try:
            memex_snippet = MEMEX_PATH.read_text(encoding="utf-8")[:2000]
        except OSError as exc:
            logger.warning("Failed to read memex context from %s: %s", MEMEX_PATH, exc)

    prompt = (
        "You are answering a question inside the Syke Pi runtime.\n\n"
        "Use the workspace sources below when forming your answer:\n"
        f"- events.db at {WORKSPACE_ROOT / 'events.db'} (read-only timeline)\n"
        f"- agent.db at {WORKSPACE_ROOT / 'agent.db'} (memories and graph)\n"
        f"- memex.md at {MEMEX_PATH}\n"
        f"- scripts/ at {WORKSPACE_ROOT / 'scripts'}\n\n"
        "Treat events.db as read-only. Prefer grounded answers based on these local sources. "
        "If needed, inspect scripts/ for helper tooling or analysis utilities.\n\n"
        f"User ID: {user_id}\n"
        f"Question: {question}\n"
    )

    if memex_snippet:
        prompt += (
            "\nMemex context snippet (first 2000 chars):\n"
            "---\n"
            f"{memex_snippet}\n"
            "---\n"
        )
    else:
        prompt += "\nMemex context snippet: memex.md not found or could not be read.\n"

    try:
        runtime = get_pi_runtime()
    except RuntimeError:
        logger.info("Pi runtime not initialized; starting a workspace-backed runtime for pi_ask")
        setup_workspace(user_id)
        runtime = start_pi_runtime(
            workspace_dir=WORKSPACE_ROOT,
            session_dir=SESSIONS_DIR,
        )

    started = time.monotonic()

    try:
        result = runtime.prompt(prompt, timeout=timeout)
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.exception("Pi ask failed for user %s", user_id)
        return (
            f"Pi ask failed: {exc}",
            {
                "duration_ms": duration_ms,
                "tool_calls": 0,
                "method": "pi",
            },
        )

    duration_ms = result.duration_ms or int((time.monotonic() - started) * 1000)
    cost_dict = {
        "duration_ms": duration_ms,
        "tool_calls": len(result.tool_calls),
        "method": "pi",
    }

    if result.ok:
        return result.output, cost_dict

    error_message = result.error or "Pi ask failed"
    return error_message, cost_dict