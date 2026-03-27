"""Compatibility shim for ask().

Syke now routes ask exclusively through the Pi runtime.
"""

from __future__ import annotations

import warnings

from syke.config import SYNC_THINKING

warnings.warn(
    "syke.distribution.ask_agent is deprecated. Import from syke.llm.backends.pi_ask "
    "or use syke.llm.runtime_switch.",
    DeprecationWarning,
    stacklevel=2,
)


class AskError(RuntimeError):
    """Compatibility error type for older imports."""


from syke.llm.backends import AskEvent
from syke.llm.backends.pi_ask import pi_ask


def ask(db, user_id: str, question: str, **kwargs):
    return pi_ask(db, user_id, question, **kwargs)


def ask_stream(db, user_id: str, question: str, **kwargs):
    return pi_ask(db, user_id, question, **kwargs)


__all__ = [
    "AskError",
    "AskEvent",
    "SYNC_THINKING",
    "ask",
    "ask_stream",
]
