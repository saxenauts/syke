"""Pydantic models shared across all layers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Event(BaseModel):
    """A single event from any platform."""

    id: str | None = None
    user_id: str = ""
    source: str  # claude-code | chatgpt | github | gmail | <custom via push>
    timestamp: datetime
    event_type: str  # conversation | commit | email | tweet | watch | ...
    title: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    external_id: str | None = None  # Source-provided dedup key
    ingested_at: datetime | None = None


class IngestionResult(BaseModel):
    """Result of an ingestion run."""

    run_id: str
    source: str
    user_id: str
    status: Literal["completed", "failed", "running"] = "completed"
    events_count: int = 0
    error: str | None = None


class ActiveThread(BaseModel):
    """An active thread of interest/work the user is engaged in."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    intensity: Literal["high", "medium", "low"] = "medium"
    platforms: list[str] = Field(default_factory=list)
    recent_signals: list[str] = Field(default_factory=list)


class VoicePattern(BaseModel):
    """How the user communicates."""

    tone: str
    vocabulary_notes: list[str] = Field(default_factory=list)
    communication_style: str = ""
    examples: list[str] = Field(default_factory=list)


class UserProfile(BaseModel):
    """DEPRECATED: UserProfile is kept for migration deserialization only. Use memex for new code."""

    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    identity_anchor: str = Field(min_length=1)  # 2-3 sentence prose summary
    active_threads: list[ActiveThread] = Field(default_factory=list)
    recent_detail: str = ""  # Precise context from last ~2 weeks
    background_context: str = ""  # Longer arcs
    world_state: str = ""  # Precise map of the user's current world — projects, statuses, decisions, blockers
    voice_patterns: VoicePattern | None = None
    sources: list[str] = Field(default_factory=list)
    events_count: int = Field(default=0, ge=0)
    model: str = "claude-opus-4-6"
    thinking_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)


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
