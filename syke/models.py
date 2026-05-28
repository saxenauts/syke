"""Pydantic models shared across all layers."""

from __future__ import annotations

from datetime import UTC, datetime

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
