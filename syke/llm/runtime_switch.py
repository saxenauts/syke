"""
Runtime switch — routes synthesis and ask operations to the active backend.

Configured via SYKE_RUNTIME env var override or ~/.syke/config.toml:
    [runtime]
    backend = "claude"   # or "pi"

The switch preserves both paths:
- "claude": Claude Agent SDK via MCP tools (existing path)
- "pi": Persistent Pi runtime with workspace (new path)
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Runtime detection ─────────────────────────────────────────────────


def get_runtime() -> str:
    """
    Get the active runtime backend.

    Priority: SYKE_RUNTIME env var > config.toml > default 'claude'.
    Returns 'claude' or 'pi'. Defaults to 'claude'.
    """
    import os

    env_val = os.environ.get("SYKE_RUNTIME", "").strip().lower()
    if env_val in ("claude", "pi"):
        return env_val

    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return "claude"

    config_path = Path.home() / ".syke" / "config.toml"
    if not config_path.exists():
        return "claude"

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        return config.get("runtime", {}).get("backend", "claude")
    except Exception:
        return "claude"


# ── Synthesis routing ─────────────────────────────────────────────────


def run_synthesis(db, user_id: str, **kwargs) -> dict:
    """
    Route synthesis to the active backend.

    Claude path: Claude Agent SDK with MCP tools (synthesis.py)
    Pi path: Persistent Pi runtime with workspace (pi_synthesis.py)
    """
    runtime = get_runtime()

    if runtime == "pi":
        logger.info("Routing synthesis to Pi runtime")
        from syke.memory.pi_synthesis import pi_synthesize

        return pi_synthesize(db, user_id, **kwargs)
    else:
        logger.info("Routing synthesis to Claude Agent SDK")
        from syke.memory.synthesis import synthesize

        return synthesize(db, user_id, **kwargs)


# ── Ask routing ───────────────────────────────────────────────────────


def run_ask(
    db,
    user_id: str,
    question: str,
    **kwargs,
) -> tuple[str, dict]:
    """
    Route ask to the active backend.

    Claude path: Claude Agent SDK ask agent (ask_agent.py)
    Pi path: Pi runtime ask (pi_ask.py)

    Returns (answer_text, cost_dict).
    """
    runtime = get_runtime()

    if runtime == "pi":
        logger.info("Routing ask to Pi runtime")
        from syke.distribution.pi_ask import pi_ask

        return pi_ask(db, user_id, question, **kwargs)
    else:
        logger.info("Routing ask to Claude Agent SDK")
        from syke.distribution.ask_agent import ask

        return ask(db, user_id, question, **kwargs)


def run_ask_stream(
    db,
    user_id: str,
    question: str,
    **kwargs,
):
    """
    Route streaming ask to the active backend.

    Only Claude path supports streaming currently.
    Pi path falls back to non-streaming.
    """
    runtime = get_runtime()

    if runtime == "pi":
        logger.info("Routing ask_stream to Pi (non-streaming fallback)")
        from syke.distribution.pi_ask import pi_ask

        answer, cost = pi_ask(db, user_id, question, **kwargs)
        yield answer
    else:
        logger.info("Routing ask_stream to Claude Agent SDK")
        from syke.distribution.ask_agent import ask_stream

        yield from ask_stream(db, user_id, question, **kwargs)
