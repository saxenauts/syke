"""Pi backend types."""

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
