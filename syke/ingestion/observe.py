from __future__ import annotations

import json
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
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
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
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
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
        seq_counter = 0
        tool_call_ids: dict[str, str] = {}

        # Build session envelope
        envelope = self._make_envelope(session)
        if self._should_insert(envelope):
            events_to_insert.append(envelope)

        # Build per-turn events
        for idx, turn in enumerate(session.turns):
            turn_event_id: str | None = None
            if turn.content:
                turn_event = self._make_turn_event(session, turn, idx, seq_counter)
                turn_event_id = str(turn_event.id)
                if self._should_insert(turn_event):
                    events_to_insert.append(turn_event)
                seq_counter += 1

            for tool_idx, tool_block in enumerate(turn.tool_calls):
                block_type = tool_block.get("block_type")
                if block_type == "tool_use":
                    tool_call_event = self._make_tool_call_event(
                        session,
                        turn,
                        turn_event_id,
                        cast(dict[str, object], tool_block),
                        idx,
                        tool_idx,
                        seq_counter,
                    )
                    if self._should_insert(tool_call_event):
                        events_to_insert.append(tool_call_event)

                    tool_id = tool_block.get("tool_id")
                    if isinstance(tool_id, str) and tool_id:
                        tool_call_ids[tool_id] = str(tool_call_event.id)
                    seq_counter += 1
                elif block_type == "tool_result":
                    tool_use_id = tool_block.get("tool_use_id")
                    parent_tool_call_id = (
                        tool_call_ids.get(tool_use_id)
                        if isinstance(tool_use_id, str) and tool_use_id
                        else None
                    )
                    tool_result_event = self._make_tool_result_event(
                        session,
                        turn,
                        parent_tool_call_id,
                        cast(dict[str, object], tool_block),
                        idx,
                        tool_idx,
                        seq_counter,
                    )
                    if self._should_insert(tool_result_event):
                        events_to_insert.append(tool_result_event)
                    seq_counter += 1

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

    def _make_turn_event(
        self,
        session: ObservedSession,
        turn: ObservedTurn,
        idx: int,
        seq_idx: int,
    ) -> Event:
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
            sequence_index=seq_idx,
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

    def _make_tool_call_event(
        self,
        session: ObservedSession,
        turn: ObservedTurn,
        turn_event_id: str | None,
        tool_block: dict[str, object],
        turn_idx: int,
        tool_idx: int,
        seq_idx: int,
    ) -> Event:
        tool_name = tool_block.get("tool_name")
        tool_id = tool_block.get("tool_id")
        tool_input = tool_block.get("input", {})

        input_payload: str
        if isinstance(tool_input, dict):
            input_payload = json.dumps(tool_input, default=str, ensure_ascii=False)
        else:
            input_payload = "{}"

        return Event(
            id=str(uuid7()),
            user_id=self.user_id,
            source=self.source,
            timestamp=turn.timestamp,
            event_type=EVENT_TYPE_TOOL_CALL,
            title=(
                str(tool_name)[:120] if isinstance(tool_name, str) and tool_name else "tool_call"
            ),
            content=input_payload,
            metadata={},
            extras={},
            external_id=f"{self.source}:{session.session_id}:tool_call:{turn_idx}:{tool_idx}",
            session_id=session.session_id,
            parent_session_id=session.parent_session_id,
            sequence_index=seq_idx,
            tool_name=tool_name if isinstance(tool_name, str) and tool_name else None,
            tool_correlation_id=tool_id if isinstance(tool_id, str) and tool_id else None,
            parent_event_id=turn_event_id,
            source_event_type="tool_use",
            source_path=str(session.source_path),
            source_line_index=cast(int | None, turn.metadata.get("source_line_index")),
        )

    def _make_tool_result_event(
        self,
        session: ObservedSession,
        turn: ObservedTurn,
        tool_call_event_id: str | None,
        tool_block: dict[str, object],
        turn_idx: int,
        tool_idx: int,
        seq_idx: int,
    ) -> Event:
        content = tool_block.get("content", "")
        text_content = content if isinstance(content, str) else ""
        tool_use_id = tool_block.get("tool_use_id")

        return Event(
            id=str(uuid7()),
            user_id=self.user_id,
            source=self.source,
            timestamp=turn.timestamp,
            event_type=EVENT_TYPE_TOOL_RESULT,
            title="tool_result",
            content=text_content,
            metadata={},
            extras={},
            external_id=f"{self.source}:{session.session_id}:tool_result:{turn_idx}:{tool_idx}",
            session_id=session.session_id,
            parent_session_id=session.parent_session_id,
            sequence_index=seq_idx,
            tool_correlation_id=(
                tool_use_id if isinstance(tool_use_id, str) and tool_use_id else None
            ),
            is_error=1 if tool_block.get("is_error") else 0,
            parent_event_id=tool_call_event_id,
            source_event_type="tool_result",
            source_path=str(session.source_path),
            source_line_index=cast(int | None, turn.metadata.get("source_line_index")),
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
        original_content = event.content
        filtered, _ = self.content_filter.process(event.content, event.title or "")
        if filtered is None:
            return None
        event.content = filtered
        # P4: Auditable redaction — mark events where content was sanitized
        if filtered != original_content:
            event.extras = {**event.extras, "content_redacted": True}
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
