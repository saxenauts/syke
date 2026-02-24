"""Tests for the database layer."""

from datetime import datetime

from syke.db import SykeDB
from syke.models import Event, UserProfile


def test_initialize(db):
    """DB initializes without error."""
    assert db.conn is not None


def test_insert_and_query_event(db, user_id):
    """Insert an event and query it back."""
    event = Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 15, 12, 0),
        event_type="test_event",
        title="Test Event",
        content="This is test content.",
    )
    assert db.insert_event(event) is True

    events = db.get_events(user_id)
    assert len(events) == 1
    assert events[0]["title"] == "Test Event"
    assert events[0]["source"] == "test"


def test_dedup(db, user_id):
    """Duplicate events are rejected."""
    event = Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 15, 12, 0),
        event_type="test_event",
        title="Duplicate",
        content="Same event.",
    )
    assert db.insert_event(event) is True
    assert db.insert_event(event) is False
    assert db.count_events(user_id) == 1


def test_count_and_sources(db, user_id):
    """Count events and list sources."""
    for i, src in enumerate(["gmail", "gmail", "github"]):
        db.insert_event(Event(
            user_id=user_id,
            source=src,
            timestamp=datetime(2025, 1, 15 + i, 12, 0),
            event_type="test",
            title=f"Event {i}",
            content=f"Content {i}",
        ))

    assert db.count_events(user_id) == 3
    assert db.count_events(user_id, "gmail") == 2
    assert set(db.get_sources(user_id)) == {"gmail", "github"}


def test_search_events(db, user_id):
    """Search events by keyword."""
    db.insert_event(Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 15, 12, 0),
        event_type="test",
        title="Python project",
        content="Working on a Python machine learning project.",
    ))
    db.insert_event(Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 16, 12, 0),
        event_type="test",
        title="Grocery list",
        content="Buy milk and eggs.",
    ))

    results = db.search_events(user_id, "Python")
    assert len(results) == 1
    assert results[0]["title"] == "Python project"



def test_ingestion_run(db, user_id):
    """Start and complete an ingestion run."""
    run_id = db.start_ingestion_run(user_id, "test")
    assert run_id

    db.complete_ingestion_run(run_id, 42)

    status = db.get_status(user_id)
    assert len(status["recent_runs"]) == 1
    assert status["recent_runs"][0]["events_count"] == 42
    assert status["recent_runs"][0]["status"] == "completed"


def test_status_empty(db, user_id):
    """Status works with no data."""
    status = db.get_status(user_id)
    assert status["total_events"] == 0
    assert status["sources"] == {}


def test_get_event_by_id(db, user_id):
    """Fetch a single event by ID."""
    event = Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 15, 12, 0),
        event_type="test",
        title="Findable Event",
        content="This event has specific content.",
    )
    db.insert_event(event)
    events = db.get_events(user_id)
    event_id = events[0]["id"]

    result = db.get_event_by_id(user_id, event_id)
    assert result is not None
    assert result["title"] == "Findable Event"
    assert result["content"] == "This event has specific content."


def test_get_event_by_id_not_found(db, user_id):
    """Non-existent event ID returns None."""
    result = db.get_event_by_id(user_id, "nonexistent-id")
    assert result is None


def test_get_event_by_id_wrong_user(db, user_id):
    """Event ID for wrong user returns None."""
    event = Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 1, 15, 12, 0),
        event_type="test",
        title="User-scoped Event",
        content="Only for the right user.",
    )
    db.insert_event(event)
    events = db.get_events(user_id)
    event_id = events[0]["id"]

    result = db.get_event_by_id("other_user", event_id)
    assert result is None


def test_status_includes_latest_event_at(db, user_id):
    """get_status() includes latest_event_at from events table."""
    status = db.get_status(user_id)
    assert status["latest_event_at"] is None  # empty DB

    event = Event(
        user_id=user_id,
        source="test",
        timestamp=datetime(2025, 6, 1),
        event_type="test",
        title="Test",
        content="Content",
    )
    db.insert_event(event)
    status = db.get_status(user_id)
    assert status["latest_event_at"] is not None


def test_migration_idempotent(tmp_path):
    """Calling initialize() twice doesn't crash (migration idempotency)."""
    db = SykeDB(tmp_path / "idem.db")
    db.initialize()
    db.initialize()  # Second call must not raise
    # Verify the DB is functional
    assert db.count_events("nobody") == 0
    db.close()

