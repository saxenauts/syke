"""Backend implementations for LLM providers.

This package contains provider-specific implementations for synthesis and ask operations.
All public access should go through syke.llm.runtime_switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class AskEvent:
    type: Literal["thinking", "text", "tool_call"]
    content: str
    metadata: dict[str, object] | None = None


__all__ = [
    "AskEvent",
]
