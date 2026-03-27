"""Pi-based agentic synthesis.

.. deprecated::
    This module has moved to syke.llm.backends.pi_synthesis.
    Import from the new location for forward compatibility.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "syke.memory.pi_synthesis is deprecated. "
    "Import from syke.llm.backends.pi_synthesis or use syke.llm.runtime_switch.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all public names from the new location
from syke.llm.backends.pi_synthesis import (
    pi_synthesize,
)

__all__ = [
    "pi_synthesize",
]
