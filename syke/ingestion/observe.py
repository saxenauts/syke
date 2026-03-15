from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.ingestion.base import ContentFilter
from syke.ingestion.constants import (
    EVENT_TYPE_INGEST_ERROR,
    EVENT_TYPE_SESSION_START,
    EVENT_TYPE_TURN,
)
from syke.models import Event, IngestionResult

logger = logging.getLogger(__name__)


@dataclass
class ObservedTurn:
    role: str  # "user" | "assistant"
    content: str  # Full text, no cap
    timestamp: datetime
    uuid: str | None = None
    parent_uuid: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservedSession:
    session_id: str
    source_path: Path
    start_time: datetime
    end_time: datetime | None = None
    project: str | None = None
    parent_session_id: str | None = None
    turns: list[ObservedTurn] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    is_subagent: bool = False
    agent_id: str | None = None
    agent_slug: str | None = None


class ObserveAdapter(ABC):
    source: str  # Override in subclass

    def __init__(self, db: SykeDB, user_id: str):
        self.db = db
        self.user_id = user_id
        self.content_filter = ContentFilter()

    @abstractmethod
    def discover(self) -> list[Path]: ...

    @abstractmethod
    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]: ...

    def ingest(self, **kwargs) -> IngestionResult:
        run_id = self.db.start_ingestion_run(self.user_id, self.source)
        count = 0

        try:
            for session in self.iter_sessions():
                try:
                    inserted = self._ingest_session(session)
                    count += inserted
                except Exception as exc:
                    # Principle 7: failures are telemetry
                    self._record_ingest_error(session, exc)
                    logger.warning("Failed to ingest session %s: %s", session.session_id, exc)

            self.db.complete_ingestion_run(run_id, count)
            return IngestionResult(
                source=self.source,
                events_count=count,
                run_id=run_id,
                user_id=self.user_id,
            )
        except Exception as e:
            self.db.complete_ingestion_run(run_id, count, error=str(e))
            raise

    def _ingest_session(self, session: ObservedSession) -> int:
        """Insert one session atomically. All turns succeed or all roll back."""
        events_to_insert: list[Event] = []

        # Build session envelope
        envelope = self._make_envelope(session)
        if self._should_insert(envelope):
            events_to_insert.append(envelope)

        # Build per-turn events
        for idx, turn in enumerate(session.turns):
            event = self._make_turn_event(session, turn, idx)
            if self._should_insert(event):
                events_to_insert.append(event)

        if not events_to_insert:
            return 0

        # Atomic insertion
        inserted = 0
        with self.db.transaction():
            for event in events_to_insert:
                filtered = self._filter_content(event)
                if filtered is None:
                    self._record_filtered_event(session, event)
                    continue
                if self.db.insert_event(filtered):
                    inserted += 1
        return inserted

    def _make_envelope(self, session: ObservedSession) -> Event:
        user_turns = sum(1 for t in session.turns if t.role == "user")
        assistant_turns = len(session.turns) - user_turns
        project_label = session.project or "unknown"
        branch = session.metadata.get("git_branch", "")
        duration = session.metadata.get("duration_minutes", 0)

        content = (
            f"Session in {project_label}"
            f"{f' | {branch}' if branch else ''}"
            f" | {duration}m"
            f" | {user_turns} user + {assistant_turns} assistant turns"
        )

        first_user = next((t for t in session.turns if t.role == "user"), None)
        title = first_user.content[:120].split("\n")[0] if first_user else session.session_id

        extras = dict(session.metadata)
        extras["project"] = session.project or extras.get("project")

        return Event(
            id=str(uuid7()),
            user_id=self.user_id,
            source=self.source,
            timestamp=session.start_time,
            event_type=EVENT_TYPE_SESSION_START,
            title=title,
            content=content,
            metadata={},
            extras=extras,
            external_id=f"{self.source}:{session.session_id}:start",
            session_id=session.session_id,
            parent_session_id=session.parent_session_id,
            source_path=str(session.source_path),
        )

    def _make_turn_event(self, session: ObservedSession, turn: ObservedTurn, idx: int) -> Event:
        title = turn.content[:120].split("\n")[0] if turn.content else f"turn-{idx}"

        turn_metadata = dict(turn.metadata)
        usage_obj = turn_metadata.pop("usage", None)
        usage = cast(dict[str, object], usage_obj) if isinstance(usage_obj, dict) else None

        model = turn_metadata.pop("model", None)
        stop_reason = turn_metadata.pop("stop_reason", None)
        source_event_type = turn_metadata.pop("source_event_type", None)
        source_line_index = turn_metadata.pop("source_line_index", None)

        model_value = model if isinstance(model, str) and model else None
        stop_reason_value = stop_reason if isinstance(stop_reason, str) and stop_reason else None
        source_event_type_value = (
            source_event_type
            if isinstance(source_event_type, str) and source_event_type
            else turn.role
        )
        source_line_index_value = source_line_index if isinstance(source_line_index, int) else None

        input_tokens = None
        output_tokens = None
        cache_read_tokens = None
        cache_creation_tokens = None
        usage_extras: dict[str, object] = {}
        if usage is not None:
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            cache_read_tokens = usage.get("cache_read_input_tokens")
            cache_creation_tokens = usage.get("cache_creation_input_tokens")
            usage_extras = {
                key: value
                for key, value in usage.items()
                if key
                not in {
                    "input_tokens",
                    "output_tokens",
                    "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                }
            }

        extras = dict(turn_metadata)
        if usage_extras:
            extras["usage"] = usage_extras

        return Event(
            id=str(uuid7()),
            user_id=self.user_id,
            source=self.source,
            timestamp=turn.timestamp,
            event_type=EVENT_TYPE_TURN,
            title=title,
            content=turn.content,
            metadata={},
            extras=extras,
            external_id=f"{self.source}:{session.session_id}:turn:{idx}",
            session_id=session.session_id,
            parent_session_id=session.parent_session_id,
            sequence_index=idx,
            role=turn.role,
            model=model_value,
            stop_reason=stop_reason_value,
            input_tokens=input_tokens if isinstance(input_tokens, int) else None,
            output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            cache_read_tokens=cache_read_tokens if isinstance(cache_read_tokens, int) else None,
            cache_creation_tokens=(
                cache_creation_tokens if isinstance(cache_creation_tokens, int) else None
            ),
            source_event_type=source_event_type_value,
            source_path=str(session.source_path),
            source_line_index=source_line_index_value,
        )

    def _should_insert(self, event: Event) -> bool:
        if event.external_id and self.db.event_exists_by_external_id(
            self.source,
            self.user_id,
            event.external_id,
        ):
            return False
        return True

    def _filter_content(self, event: Event) -> Event | None:
        filtered, _ = self.content_filter.process(event.content, event.title or "")
        if filtered is None:
            return None
        event.content = filtered
        return event

    def _record_filtered_event(self, session: ObservedSession, event: Event) -> None:
        with suppress(Exception):
            anomaly = Event(
                id=str(uuid7()),
                user_id=self.user_id,
                source=self.source,
                timestamp=event.timestamp,
                event_type=EVENT_TYPE_INGEST_ERROR,
                title=f"Content filtered: {event.title or 'unknown'}",
                content=f"Event filtered by content policy. Original type: {event.event_type}",
                metadata={},
                extras={
                    "session_id": session.session_id,
                    "original_event_type": event.event_type,
                    "original_external_id": event.external_id,
                    "filter_reason": "content_policy",
                },
                external_id=(
                    f"{self.source}:{session.session_id}:filtered:{event.external_id or 'unknown'}"
                ),
                session_id=session.session_id,
                parent_session_id=session.parent_session_id,
                is_error=1,
                source_event_type=event.event_type,
                source_path=str(session.source_path),
            )
            _ = self.db.insert_event(anomaly)

    def _record_ingest_error(self, session: ObservedSession, error: Exception) -> None:
        with suppress(Exception):
            event = Event(
                id=str(uuid7()),
                user_id=self.user_id,
                source=self.source,
                timestamp=datetime.now(UTC),
                event_type=EVENT_TYPE_INGEST_ERROR,
                title=f"Ingest error: {session.source_path.name}",
                content=f"Session {session.session_id}: {type(error).__name__}: {error}",
                metadata={},
                extras={
                    "session_id": session.session_id,
                    "source_path": str(session.source_path),
                    "error_type": type(error).__name__,
                },
                external_id=f"{self.source}:{session.session_id}:error",
                session_id=session.session_id,
                parent_session_id=session.parent_session_id,
                is_error=1,
                source_path=str(session.source_path),
            )
            _ = self.db.insert_event(event)
