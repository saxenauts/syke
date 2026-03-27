"""Compatibility shim for synthesis.

Syke now routes synthesis exclusively through the Pi runtime.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "syke.memory.synthesis is deprecated. Import from syke.llm.backends.pi_synthesis "
    "or use syke.llm.runtime_switch.",
    DeprecationWarning,
    stacklevel=2,
)

from syke.llm.backends.pi_synthesis import pi_synthesize as synthesize

__all__ = ["synthesize"]
