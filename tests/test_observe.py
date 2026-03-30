from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from uuid_extensions import uuid7

from syke.db import SykeDB
from syke.observe.adapter import (
    EVENT_TYPE_INGEST_ERROR,
    EVENT_TYPE_SESSION_START,
    EVENT_TYPE_TURN,
    ObserveAdapter,
    ObservedSession,
    ObservedTurn,
)
from syke.observe.parsers import (
    extract_text_content,
    parse_timestamp,
    read_jsonl,
)
from syke.models import Event


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_turn(
    role: str, content: str, ts: datetime | None = None, **metadata: object
) -> ObservedTurn:
    return ObservedTurn(
        role=role,
        content=content,
        timestamp=ts or datetime.now(UTC),
        metadata=dict(metadata),
    )


def _make_event(user_id: str, **overrides: Any) -> Event:
    defaults: dict[str, Any] = {
        "id": str(uuid7()),
        "user_id": user_id,
        "source": "claude-code",
        "timestamp": datetime.now(UTC),
        "event_type": "session",
        "title": "Test event",
        "content": "Test content",
        "metadata": {},
    }
    defaults.update(overrides)
    return Event(**defaults)


class _TestObserveAdapter(ObserveAdapter):
    source = "test-observe"

    def __init__(self, db: SykeDB, user_id: str, sessions: list[ObservedSession] | None = None):
        super().__init__(db, user_id)
        self._sessions = sessions or []

    def discover(self) -> list[Path]:
        return []

    def iter_sessions(self, since: float = 0, paths: Iterable[Path] | None = None):
        _ = paths
        return iter(self._sessions)


class _FailingSessionAdapter(_TestObserveAdapter):
    def _ingest_session(self, session: ObservedSession) -> int:  # type: ignore[override]
        raise ValueError("parse failure")


def test_read_jsonl_parses_valid_lines(tmp_path):
    """read_jsonl returns all valid JSON objects from a JSONL file."""
    fpath = tmp_path / "valid.jsonl"
    _write_jsonl(fpath, ['{"a": 1}', '{"b": "two"}'])

    rows = read_jsonl(fpath)

    assert rows == [{"a": 1}, {"b": "two"}]


def test_read_jsonl_skips_malformed_lines(tmp_path):
    """read_jsonl skips malformed lines and keeps parseable lines."""
    fpath = tmp_path / "mixed.jsonl"
    _write_jsonl(fpath, ['{"a": 1}', "{bad-json}", '{"c": 3}'])

    rows = read_jsonl(fpath)

    assert rows == [{"a": 1}, {"c": 3}]


def test_parse_timestamp_iso_string():
    """parse_timestamp parses ISO timestamps with Z suffix into UTC datetime."""
    ts = parse_timestamp({"timestamp": "2026-01-02T03:04:05Z"})

    assert ts == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_parse_timestamp_epoch_millis():
    """parse_timestamp parses epoch milliseconds into UTC datetime."""
    ts = parse_timestamp({"timestamp": 1_700_000_000_000})

    assert ts == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_parse_timestamp_returns_none_for_missing():
    """parse_timestamp returns None when timestamp is absent or empty."""
    assert parse_timestamp({}) is None
    assert parse_timestamp({"timestamp": ""}) is None


def test_extract_text_content_string_message():
    """extract_text_content returns message.content when it is a plain string."""
    line = cast(dict[str, object], {"message": {"content": "hello world"}})

    assert extract_text_content(line) == "hello world"


def test_extract_text_content_block_list():
    """extract_text_content joins text blocks and string blocks from list content."""
    line = cast(
        dict[str, object],
        {
            "message": {
                "content": [
                    {"type": "text", "text": "Line 1"},
                    "Line 2",
                    {"type": "image", "url": "ignored"},
                    {"type": "text", "text": "Line 3"},
                ]
            }
        },
    )

    assert extract_text_content(line) == "Line 1\nLine 2\nLine 3"



def test_event_session_id_round_trip(db, user_id):
    """Event session_id and parent_session_id persist and round-trip from the DB."""
    event = _make_event(
        user_id,
        source="test-observe",
        event_type=EVENT_TYPE_TURN,
        content="x" * 80,
        session_id="ses_round_trip",
        parent_session_id="ses_parent",
    )

    assert db.insert_event(event)
    row = db.get_event_by_id(user_id, event.id or "")

    assert row is not None
    assert row["session_id"] == "ses_round_trip"
    assert row["parent_session_id"] == "ses_parent"


def test_event_session_id_nullable(db, user_id):
    """session_id and parent_session_id accept NULL values in stored events."""
    event = _make_event(
        user_id, source="test-observe", event_type=EVENT_TYPE_TURN, content="x" * 80
    )

    assert db.insert_event(event)
    row = db.get_event_by_id(user_id, event.id or "")

    assert row is not None
    assert row["session_id"] is None
    assert row["parent_session_id"] is None


def test_migration_adds_session_columns(tmp_path):
    """Database migration adds session_id and parent_session_id to pre-phase2 schema."""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source, user_id, timestamp, title)
        );
        """
    )
    conn.commit()
    conn.close()

    with SykeDB(db_path) as migrated_db:
        columns = {
            row[1] for row in migrated_db.conn.execute("PRAGMA table_info(events)").fetchall()
        }

    assert "session_id" in columns
    assert "parent_session_id" in columns


def test_observe_per_turn_events(db, user_id):
    """4-turn session ingests as 5 events (1 envelope + 4 turns) with same session_id."""
    turns = [
        _make_turn("user", "U" * 70),
        _make_turn("assistant", "A" * 70),
        _make_turn("user", "U2" * 40),
        _make_turn("assistant", "A2" * 40),
    ]
    session = ObservedSession(
        session_id="ses_per_turn",
        source_path=Path("/tmp/ses_per_turn.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        project="proj-a",
        turns=turns,
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    result = adapter.ingest()
    rows = db.get_events(user_id, source="test-observe", limit=20)

    assert result.events_count == 5
    assert len(rows) == 5
    assert {row["session_id"] for row in rows} == {"ses_per_turn"}
    assert sum(1 for row in rows if row["event_type"] == EVENT_TYPE_SESSION_START) == 1
    assert sum(1 for row in rows if row["event_type"] == EVENT_TYPE_TURN) == 4


def test_observe_no_content_cap(db, user_id):
    """Turn event ingestion preserves full 60K content with no truncation."""
    long_content = "x" * 60_000
    session = ObservedSession(
        session_id="ses_long",
        source_path=Path("/tmp/ses_long.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[_make_turn("user", long_content)],
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    result = adapter.ingest()
    row = db.conn.execute(
        "SELECT content FROM events WHERE user_id = ? AND source = ? AND event_type = ?",
        (user_id, "test-observe", EVENT_TYPE_TURN),
    ).fetchone()

    assert result.events_count == 2
    assert row is not None
    assert len(row[0]) == 60_000
    assert row[0] == long_content


def test_observe_dedup_via_external_id(db, user_id):
    """Re-ingesting identical observed session creates zero new events via external_id dedup."""
    session = ObservedSession(
        session_id="ses_dedup",
        source_path=Path("/tmp/ses_dedup.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[_make_turn("user", "x" * 80), _make_turn("assistant", "y" * 80)],
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    first = adapter.ingest()
    second = adapter.ingest()

    assert first.events_count == 3
    assert second.events_count == 0
    assert db.count_events(user_id, source="test-observe") == 3


def test_observe_session_envelope_metadata(db, user_id):
    """Session envelope stores structured metadata as JSON content (P1 compliant)."""
    session = ObservedSession(
        session_id="ses_meta",
        source_path=Path("/tmp/ses_meta.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        project="~/repo/app",
        turns=[_make_turn("user", "x" * 90), _make_turn("assistant", "y" * 90)],
        metadata={"git_branch": "main", "duration_minutes": 42},
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    adapter.ingest()
    row = db.conn.execute(
        "SELECT content, metadata FROM events WHERE user_id = ? AND event_type = ?",
        (user_id, EVENT_TYPE_SESSION_START),
    ).fetchone()

    assert row is not None
    envelope = json.loads(row[0])
    assert envelope["session_id"] == "ses_meta"
    assert envelope["project"] == "~/repo/app"
    assert envelope["source_path"] == "/tmp/ses_meta.jsonl"
    assert envelope["start_time"] is not None
    assert envelope["end_time"] is None


def test_observe_subagent_detection(db, user_id):
    """Observed subagent sessions propagate parent_session_id to all emitted events."""
    session = ObservedSession(
        session_id="ses_child",
        parent_session_id="ses_parent",
        source_path=Path("/tmp/ses_child.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[_make_turn("user", "x" * 80), _make_turn("assistant", "y" * 80)],
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    adapter.ingest()
    rows = db.conn.execute(
        "SELECT parent_session_id FROM events WHERE user_id = ? AND source = ?",
        (user_id, "test-observe"),
    ).fetchall()

    assert rows
    assert {row[0] for row in rows} == {"ses_parent"}


def test_observe_empty_session_skipped(db, user_id):
    """Session re-ingestion with no turns produces no additional events."""
    empty = ObservedSession(
        session_id="ses_empty",
        source_path=Path("/tmp/ses_empty.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[],
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[empty])

    first = adapter.ingest()
    second = adapter.ingest()

    assert first.events_count == 1
    assert second.events_count == 0


def test_observe_tool_call_content_is_sanitized_without_dropping_event(db, user_id):
    """Tool-call payloads are kept as events and credential-like strings are redacted."""
    turn = ObservedTurn(
        role="assistant",
        content="Calling a tool now.",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        tool_calls=[
            {
                "block_type": "tool_use",
                "tool_name": "fetch_secret",
                "tool_id": "tool-1",
                "input": {"api_key": "sk-" + ("a" * 24)},
            }
        ],
    )
    session = ObservedSession(
        session_id="ses_tool_call_redaction",
        source_path=Path("/tmp/ses_tool_call_redaction.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[turn],
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    result = adapter.ingest()
    row = db.conn.execute(
        """
        SELECT content, extras
        FROM events
        WHERE user_id = ? AND source = ? AND event_type = 'tool_call'
        """,
        (user_id, "test-observe"),
    ).fetchone()

    assert result.events_count == 3
    assert row is not None
    assert "[REDACTED]" in row["content"]
    assert "sk-" + ("a" * 24) not in row["content"]
    assert json.loads(row["extras"] or "{}")["content_redacted"] is True


def test_observe_ingest_error_creates_anomaly_event(db, user_id):
    """Per-session ingestion failure emits ingest.error with session provenance metadata."""
    session = ObservedSession(
        session_id="ses_err",
        source_path=Path("/tmp/ses_err.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[_make_turn("user", "x" * 80)],
    )
    adapter = _FailingSessionAdapter(db, user_id, sessions=[session])

    result = adapter.ingest()
    row = db.conn.execute(
        "SELECT event_type, metadata, session_id, content FROM events WHERE user_id = ? AND source = ?",
        (user_id, "test-observe"),
    ).fetchone()

    assert result.events_count == 0
    assert row is not None
    assert row["event_type"] == EVENT_TYPE_INGEST_ERROR
    assert row["session_id"] == "ses_err"
    assert "ValueError" in row["content"]
    meta = json.loads(row["metadata"] or "{}")
    assert meta["session_id"] == "ses_err"
    assert meta["source_path"].endswith("ses_err.jsonl")
    assert meta["error_type"] == "ValueError"


def test_observe_atomic_rollback_on_failure(db, user_id, monkeypatch):
    """When insert fails mid-session, transaction rollback leaves no partial session writes."""
    session = ObservedSession(
        session_id="ses_atomic",
        source_path=Path("/tmp/ses_atomic.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[_make_turn("user", "x" * 90), _make_turn("assistant", "y" * 90)],
    )
    adapter = _TestObserveAdapter(db, user_id)

    calls = {"n": 0}

    def _insert_then_fail(event: Event) -> bool:
        if event.id is None:
            event.id = str(uuid7())
        calls["n"] += 1
        db.conn.execute(
            """INSERT INTO events (id, user_id, source, timestamp, event_type, title,
               content, metadata, external_id, session_id, parent_session_id, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.user_id,
                event.source,
                event.timestamp.isoformat(),
                event.event_type,
                event.title,
                event.content,
                json.dumps(event.metadata),
                event.external_id,
                event.session_id,
                event.parent_session_id,
                datetime.now(UTC).isoformat(),
            ),
        )
        if calls["n"] == 2:
            raise RuntimeError("boom during insert")
        return True

    monkeypatch.setattr(db, "insert_event", _insert_then_fail)

    with pytest.raises(RuntimeError, match="boom during insert"):
        adapter._ingest_session(session)

    count = db.conn.execute(
        "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND session_id = ?",
        (user_id, "test-observe", "ses_atomic"),
    ).fetchone()[0]
    assert count == 0


def test_db_transaction_rolls_back_on_error(db):
    """SykeDB.transaction rolls back all writes in the block when an exception is raised."""
    with pytest.raises(RuntimeError, match="force rollback"):
        with db.transaction():
            db.conn.execute(
                """INSERT INTO events (id, user_id, source, timestamp, event_type, title,
                   content, metadata, external_id, session_id, parent_session_id, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid7()),
                    "u1",
                    "test",
                    datetime.now(UTC).isoformat(),
                    "turn",
                    "txn",
                    "body",
                    "{}",
                    None,
                    None,
                    None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            raise RuntimeError("force rollback")

    count = db.conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", ("u1",)).fetchone()[0]
    assert count == 0


def _compute_instance_id(source: str, root_path: Path, relative_path: str) -> str:
    return hashlib.sha256(f"{source}:{root_path}:{relative_path}".encode()).hexdigest()[:12]


def test_instance_id_different_roots(tmp_path):
    """Two files with the same name in different root directories produce different source_instance_ids."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()

    filename = "session-abc123.jsonl"
    source = "claude-code"

    id_a = _compute_instance_id(source, root_a, filename)
    id_b = _compute_instance_id(source, root_b, filename)

    assert id_a != id_b
    assert len(id_a) == 12
    assert len(id_b) == 12


def test_instance_id_stable(tmp_path):
    """The same file path always produces the same source_instance_id across multiple calls."""
    root = tmp_path / "sessions"
    root.mkdir()
    filename = "session-abc123.jsonl"
    source = "codex"

    id_first = _compute_instance_id(source, root, filename)
    id_second = _compute_instance_id(source, root, filename)
    id_third = _compute_instance_id(source, root, filename)

    assert id_first == id_second == id_third


def test_instance_id_in_events(db, user_id):
    """Ingested events carry the source_instance_id from the ObservedSession."""
    instance_id = "abc123def456"
    session = ObservedSession(
        session_id="ses_instance",
        source_path=Path("/tmp/ses_instance.jsonl"),
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        turns=[_make_turn("user", "x" * 80), _make_turn("assistant", "y" * 80)],
        source_instance_id=instance_id,
    )
    adapter = _TestObserveAdapter(db, user_id, sessions=[session])

    adapter.ingest()
    rows = db.conn.execute(
        "SELECT source_instance_id FROM events WHERE user_id = ? AND source = ?",
        (user_id, "test-observe"),
    ).fetchall()

    assert rows
    assert all(row[0] == instance_id for row in rows)
