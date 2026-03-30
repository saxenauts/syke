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
from syke.models import Event, IngestionResult
from syke.observe.content_filter import ContentFilter

MAX_TITLE_CHARS = 120
EVENT_TYPE_SESSION_START = "session.start"
EVENT_TYPE_TURN = "turn"
EVENT_TYPE_TOOL_CALL = "tool_call"
EVENT_TYPE_TOOL_RESULT = "tool_result"
EVENT_TYPE_INGEST_ERROR = "ingest.error"

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
    source_instance_id: str | None = None


class ObserveAdapter(ABC):
    source: str  # Override in subclass

    def __init__(self, db: SykeDB, user_id: str):
        self.db = db
        self.user_id = user_id
        self.content_filter = ContentFilter()

    @abstractmethod
    def discover(self) -> list[Path]: ...

    @abstractmethod
    def iter_sessions(
        self,
        since: float = 0,
        paths: Iterable[Path] | None = None,
    ) -> Iterable[ObservedSession]: ...

    def ingest(self, **kwargs) -> IngestionResult:
        run_id = self.db.start_ingestion_run(self.user_id, self.source)
        count = 0
        raw_paths = kwargs.get("paths")
        paths: tuple[Path, ...] | None = None
        if isinstance(raw_paths, (str, Path)):
            raw_paths = (raw_paths,)
        if isinstance(raw_paths, Iterable) and not isinstance(raw_paths, bytes):
            normalized_paths = tuple(
                Path(candidate)
                for candidate in raw_paths
                if isinstance(candidate, (str, Path))
            )
            if normalized_paths:
                paths = normalized_paths

        # Use last completed ingestion time as cursor — only process new data.
        # This makes reconcile incremental per the architecture design.
        since = 0.0
        last_sync = self.db.get_last_sync_timestamp(self.user_id, self.source)
        if last_sync:
            from datetime import UTC, datetime

            try:
                dt = datetime.fromisoformat(last_sync)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                since = dt.timestamp()
            except (ValueError, TypeError):
                since = 0.0

        try:
            for session in self.iter_sessions(since=since, paths=paths):
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
        all_events = self.session_to_events(session)
        events_to_insert = [e for e in all_events if self._should_insert(e)]

        if not events_to_insert:
            return 0

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

    def session_to_events(self, session: ObservedSession) -> list[Event]:
        """Convert a session into Event objects without inserting them."""
        events: list[Event] = []
        tool_call_ids: dict[str, str] = {}
        seq_counter = 0

        envelope = self._make_envelope(session)
        events.append(envelope)

        for turn_idx, turn in enumerate(session.turns):
            turn_event_id: str | None = None
            if turn.content:
                turn_event = self._make_turn_event(session, turn, turn_idx, seq_counter)
                events.append(turn_event)
                turn_event_id = turn_event.id
                seq_counter += 1

            for tool_idx, tool_block in enumerate(turn.tool_calls):
                block_type = tool_block.get("block_type")
                if block_type == "tool_use":
                    tool_call_event = self._make_tool_call_event(
                        session, turn, turn_event_id,
                        cast(dict[str, object], tool_block),
                        turn_idx, tool_idx, seq_counter,
                    )
                    events.append(tool_call_event)
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
                        session, turn, parent_tool_call_id,
                        cast(dict[str, object], tool_block),
                        turn_idx, tool_idx, seq_counter,
                    )
                    events.append(tool_result_event)
                    seq_counter += 1

        return events

    def _normalize_candidate_paths(
        self,
        paths: Iterable[Path] | None,
    ) -> list[Path] | None:
        if paths is None:
            return None
        normalized: list[Path] = []
        seen: set[Path] = set()
        for candidate in paths:
            if not isinstance(candidate, (str, Path)):
                continue
            path = Path(candidate).expanduser()
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not resolved.is_file() or resolved in seen:
                continue
            seen.add(resolved)
            normalized.append(resolved)
        return normalized

    def _make_envelope(self, session: ObservedSession) -> Event:
        # P1: No inferred semantics. Content is structured metadata from the
        # source artifact — no computed summaries, no turn counting.
        envelope_data = {
            "session_id": session.session_id,
            "project": session.project,
            "source_path": str(session.source_path),
            "start_time": session.start_time.isoformat(),
            "end_time": session.end_time.isoformat() if session.end_time else None,
        }
        content = json.dumps(envelope_data, default=str, ensure_ascii=False)

        first_user = next((t for t in session.turns if t.role == "user"), None)
        title = first_user.content[:MAX_TITLE_CHARS].split("\n")[0] if first_user else session.session_id

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
            source_instance_id=session.source_instance_id,
        )

    def _make_turn_event(
        self,
        session: ObservedSession,
        turn: ObservedTurn,
        idx: int,
        seq_idx: int,
    ) -> Event:
        title = turn.content[:MAX_TITLE_CHARS].split("\n")[0] if turn.content else f"turn-{idx}"

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
            source_instance_id=session.source_instance_id,
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
                str(tool_name)[:MAX_TITLE_CHARS] if isinstance(tool_name, str) and tool_name else "tool_call"
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
        """Apply content filter, but never drop tool calls — structure matters."""
        original_content = event.content

        # Tool calls/results: keep even if content empty, but still sanitize
        if event.event_type in (EVENT_TYPE_TOOL_CALL, EVENT_TYPE_TOOL_RESULT):
            if event.content:
                filtered = self.content_filter.sanitize(event.content)
                event.content = filtered
                if filtered != original_content:
                    event.extras = {**event.extras, "content_redacted": True}
            return event

        # Other events: full skip/sanitize logic
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
