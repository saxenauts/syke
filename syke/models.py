"""Pydantic models shared across all layers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Memory layer models (storage branch)
# ---------------------------------------------------------------------------


class Memory(BaseModel):
    """A single meme: any unit of knowledge the agent extracts or creates.

    Everything is a memory: a person, a relationship, a project, a preference,
    a story thread, a todo. Free-form text, agent-written.
    """

    id: str
    user_id: str
    content: str  # Free-form text, agent-written
    source_event_ids: list[str] = Field(default_factory=list)  # Evidence pointers
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    updated_at: datetime | None = None
    superseded_by: str | None = None  # Points to newer version (old version deactivated)
    active: bool = True  # False = decayed/archived


class Link(BaseModel):
    """Sparse connection between memories with natural language reason.

    No typed relationships. The reason field IS the type.
    The agent reads it and knows what it means.
    """

    id: str
    user_id: str
    source_id: str  # Memory ID
    target_id: str  # Memory ID or event ID
    reason: str  # Natural language, agent-written
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class MemoryOp(BaseModel):
    """Operation log entry (audit trail for memory operations).

    Every memory operation is logged: add, link, update, retrieve, compact.
    These logs are used for synthesis gating and debugging.
    """

    id: str
    user_id: str
    operation: str  # add | link | update | retrieve | compact | synthesize
    input_summary: str = ""
    output_summary: str = ""
    memory_ids: list[str] = Field(default_factory=list)  # Memories involved
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
