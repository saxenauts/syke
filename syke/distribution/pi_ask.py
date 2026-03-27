"""Pi-based ask implementation.

.. deprecated::
    This module has moved to syke.llm.backends.pi_ask.
    Import from the new location for forward compatibility.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "syke.distribution.pi_ask is deprecated. "
    "Import from syke.llm.backends.pi_ask or use syke.llm.runtime_switch.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all public names from the new location
from syke.llm.backends.pi_ask import (
    pi_ask,
)

__all__ = [
    "pi_ask",
]
