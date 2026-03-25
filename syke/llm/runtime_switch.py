"""Runtime switch — routes ask and synthesis to the active runtime backend.

Reads the ``runtime`` key from ``~/.syke/config.toml`` (default ``"claude"``)
and dispatches to the appropriate implementation module.

Supported runtimes:
  * ``claude`` — Claude Agent SDK via ask_agent / synthesis
  * ``pi``     — lightweight Pi runtime via pi_ask / pi_synthesis
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from syke.db import SykeDB

log = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".syke" / "config.toml"
_DEFAULT_RUNTIME = "claude"
_VALID_RUNTIMES = {"claude", "pi"}


def get_runtime() -> str:
    """Return the active runtime name from ``~/.syke/config.toml``.

    Reads the file as raw TOML and looks for a top-level ``runtime`` key.
    Falls back to ``"claude"`` if the file is missing, unreadable, or the
    key is absent.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        raw = _CONFIG_PATH.read_bytes()
        cfg = tomllib.loads(raw.decode("utf-8"))
    except (FileNotFoundError, OSError) as exc:
        log.debug("Could not read %s (%s), defaulting to '%s'", _CONFIG_PATH, exc, _DEFAULT_RUNTIME)
        return _DEFAULT_RUNTIME

    runtime = cfg.get("runtime", _DEFAULT_RUNTIME)
    if runtime not in _VALID_RUNTIMES:
        log.warning("Unknown runtime '%s' in %s, falling back to '%s'", runtime, _CONFIG_PATH, _DEFAULT_RUNTIME)
        return _DEFAULT_RUNTIME

    return runtime


# ── Ask routing ─────────────────────────────────────────────────────────────


def run_ask(
    question: str,
    on_event: Callable[..., None] | None = None,
) -> tuple[str, dict[str, float]]:
    """Route an ask query to the active runtime.

    Parameters
    ----------
    question:
        The user's natural-language question.
    on_event:
        Optional streaming callback.  For the *claude* runtime this is
        ``Callable[[AskEvent], None]``; for *pi* the shape may differ.

    Returns
    -------
    tuple[str, dict[str, float]]
        ``(answer_text, cost_summary)``
    """
    runtime = get_runtime()

    if runtime == "pi":
        from syke.distribution.pi_ask import ask as pi_ask

        return pi_ask(question, on_event=on_event)

    # claude (default)
    from syke.config import DEFAULT_USER, user_db_path
    from syke.db import SykeDB as _DB
    from syke.distribution.ask_agent import ask as claude_ask
    from syke.distribution.ask_agent import ask_stream as claude_ask_stream

    user_id = DEFAULT_USER
    db = _DB(user_db_path(user_id))

    if on_event is not None:
        return claude_ask_stream(db, user_id, question, on_event)
    return claude_ask(db, user_id, question)


# ── Synthesis routing ───────────────────────────────────────────────────────


def run_synthesis(
    db: SykeDB,
    user_id: str,
    skill_override: str | None = None,
) -> dict[str, Any]:
    """Route synthesis to the active runtime.

    Parameters
    ----------
    db:
        Open SykeDB handle.
    user_id:
        The user whose events should be synthesized.
    skill_override:
        Optional skill prompt override (claude runtime only).

    Returns
    -------
    dict[str, Any]
        Status dict with at least ``{"status": …}``.
    """
    runtime = get_runtime()

    if runtime == "pi":
        from syke.memory.pi_synthesis import synthesize as pi_synthesize

        return pi_synthesize(db, user_id)

    # claude (default)
    from syke.memory.synthesis import synthesize

    return synthesize(db, user_id, skill_override=skill_override)
