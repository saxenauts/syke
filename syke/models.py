"""Pydantic models shared across all layers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Event(BaseModel):
    """A single event from any platform — the universal primitive of Observe."""

    id: str | None = None
    user_id: str = ""
    source: str
    timestamp: datetime
    event_type: str
    content: str
    title: str | None = None
    external_id: str | None = None
    ingested_at: datetime | None = None

    session_id: str | None = None
    parent_session_id: str | None = None
    sequence_index: int | None = None

    role: str | None = None
    model: str | None = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    tool_name: str | None = None
    tool_correlation_id: str | None = None
    is_error: int = 0
    duration_ms: int | None = None

    parent_event_id: str | None = None

    source_event_type: str | None = None
    source_path: str | None = None
    source_line_index: int | None = None
    source_instance_id: str | None = None

    extras: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        raw_metadata = payload.pop("metadata", None)
        raw_extras = payload.get("extras")

        metadata = raw_metadata if isinstance(raw_metadata, dict) else None
        extras = raw_extras if isinstance(raw_extras, dict) else None
        has_metadata_payload = bool(metadata)
        has_extras_payload = bool(extras)

        if extras is None:
            payload["extras"] = dict(metadata or {})
            return payload

        if has_metadata_payload and has_extras_payload and metadata != extras:
            raise ValueError("Event payload is ambiguous: use extras only")

        payload["extras"] = dict(extras)
        return payload

    @property
    def metadata(self) -> dict[str, Any]:
        return self.extras


class IngestionResult(BaseModel):
    """Result of an ingestion run."""

    run_id: str
    source: str
    user_id: str
    status: Literal["completed", "failed", "running"] = "completed"
    events_count: int = 0
    error: str | None = None


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
