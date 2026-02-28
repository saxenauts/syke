"""Tests for IngestGateway and push_event flow."""

from __future__ import annotations

from datetime import datetime, timezone

from syke.ingestion.gateway import IngestGateway


def test_push_event_basic(db, user_id):
    """Push a single event via gateway, verify it's in the DB."""
    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="test",
        event_type="note",
        title="Hello world",
        content="This is a test event pushed through the gateway.",
    )
    assert result["status"] == "ok"
    assert result["duplicate"] is False
    assert "event_id" in result

    # Verify in DB
    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    assert events[0]["title"] == "Hello world"


def test_push_event_content_filter(db, user_id):
    """Push event with credentials — they should be sanitized."""
    gw = IngestGateway(db, user_id)
    # Use a Bearer token pattern that the content filter will catch and redact
    result = gw.push(
        source="test",
        event_type="note",
        title="Secret stuff",
        content="My token is Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.longtoken and that should be stripped.",
    )
    assert result["status"] == "ok"

    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    # Credentials should be redacted
    assert "eyJhbGciOiJ" not in events[0]["content"]
    assert "[REDACTED]" in events[0]["content"]


def test_push_event_dedup_external_id(db, user_id):
    """Push same external_id twice — second should be a duplicate."""
    gw = IngestGateway(db, user_id)

    result1 = gw.push(
        source="test",
        event_type="note",
        title="First push",
        content="Content for dedup test.",
        external_id="ext-123",
    )
    assert result1["status"] == "ok"

    result2 = gw.push(
        source="test",
        event_type="note",
        title="Second push",
        content="Different content but same external_id.",
        external_id="ext-123",
    )
    assert result2["status"] == "duplicate"
    assert result2["duplicate"] is True

    # Only one event in DB
    assert db.count_events(user_id) == 1


def test_push_event_dedup_natural_key(db, user_id):
    """Push same (source, user, timestamp, title) twice — second is duplicate via DB constraint."""
    gw = IngestGateway(db, user_id)
    ts = "2025-06-15T10:00:00"

    result1 = gw.push(
        source="test",
        event_type="note",
        title="Same title",
        content="First version of content.",
        timestamp=ts,
    )
    assert result1["status"] == "ok"

    result2 = gw.push(
        source="test",
        event_type="note",
        title="Same title",
        content="Second version of content.",
        timestamp=ts,
    )
    assert result2["status"] == "duplicate"

    assert db.count_events(user_id) == 1


def test_push_batch(db, user_id):
    """Push array of events, verify counts."""
    gw = IngestGateway(db, user_id)
    events = [
        {"source": "test", "event_type": "note", "title": f"Batch {i}", "content": f"Batch content {i}"}
        for i in range(5)
    ]
    result = gw.push_batch(events)

    assert result["status"] == "ok"
    assert result["inserted"] == 5
    assert result["duplicates"] == 0
    assert result["filtered"] == 0
    assert result["total"] == 5

    assert db.count_events(user_id) == 5


def test_push_event_missing_fields(db, user_id):
    """Missing required fields should return an error."""
    gw = IngestGateway(db, user_id)

    # Missing content
    result = gw.push(source="test", event_type="note", title="No content", content="")
    assert result["status"] == "error"

    # Missing source
    result = gw.push(source="", event_type="note", title="No source", content="Some content")
    assert result["status"] == "error"

    # Missing event_type
    result = gw.push(source="test", event_type="", title="No type", content="Some content")
    assert result["status"] == "error"


def test_push_event_with_metadata(db, user_id):
    """Push event with metadata dict — should be stored and round-trip correctly."""
    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="test",
        event_type="observation",
        title="With metadata",
        content="Event that has metadata attached.",
        metadata={"mood": "curious", "confidence": 0.9},
    )
    assert result["status"] == "ok"

    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    meta = events[0].get("metadata")
    if isinstance(meta, str):
        import json
        meta = json.loads(meta)
    assert meta["mood"] == "curious"
    assert meta["confidence"] == 0.9


def test_push_event_logs_on_success(db, user_id, caplog):
    """Successful push emits an info log line."""
    import logging

    caplog.set_level(logging.INFO)
    gw = IngestGateway(db, user_id)
    result = gw.push(source="test", event_type="obs", title="hello", content="world")
    assert result["status"] == "ok"
    assert "Push: test/obs — hello" in caplog.text


def test_push_event_with_timestamp(db, user_id):
    """Push event with explicit ISO timestamp."""
    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="test",
        event_type="note",
        title="Backdated",
        content="Event from the past.",
        timestamp="2024-01-15T09:30:00",
    )
    assert result["status"] == "ok"

    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    assert "2024-01-15" in events[0]["timestamp"]


def test_push_event_invalid_timestamp(db, user_id):
    """Invalid timestamp returns error, doesn't crash."""
    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="test",
        event_type="note",
        title="Bad ts",
        content="Some content.",
        timestamp="not-a-date",
    )
    assert result["status"] == "error"
    assert "Invalid timestamp" in result["error"]


def test_push_event_list_metadata(db, user_id):
    """push() with list metadata returns error, doesn't crash."""
    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="test",
        event_type="note",
        title="List meta",
        content="Has list metadata.",
        metadata=[1, 2, 3],
    )
    assert result["status"] == "error"
    assert "dict" in result["error"]


def test_push_batch_string_metadata(db, user_id):
    """Batch with string metadata — should parse JSON string and insert."""
    gw = IngestGateway(db, user_id)
    events = [
        {
            "source": "test",
            "event_type": "observation",
            "title": "String meta",
            "content": "Event with JSON string metadata.",
            "metadata": '{"key": "value", "num": 42}',
        }
    ]
    result = gw.push_batch(events)
    assert result["status"] == "ok"
    assert result["inserted"] == 1

    stored = db.get_events(user_id, source="test")
    assert len(stored) == 1
    meta = stored[0].get("metadata")
    if isinstance(meta, str):
        import json
        meta = json.loads(meta)
    assert meta["key"] == "value"
    assert meta["num"] == 42


def test_push_batch_invalid_metadata_json(db, user_id):
    """Batch with invalid metadata JSON string — error, no crash."""
    gw = IngestGateway(db, user_id)
    events = [
        {
            "source": "test",
            "event_type": "note",
            "title": "Bad JSON meta",
            "content": "Has broken JSON metadata.",
            "metadata": "not-valid-json{",
        }
    ]
    result = gw.push_batch(events)
    assert result["status"] == "partial_error"
    assert result["inserted"] == 0
    assert len(result["errors"]) == 1
    assert "metadata" in result["errors"][0]["error"].lower() or "json" in result["errors"][0]["error"].lower()


def test_push_batch_list_metadata(db, user_id):
    """Batch with list metadata — error, no crash."""
    gw = IngestGateway(db, user_id)
    events = [
        {
            "source": "test",
            "event_type": "note",
            "title": "List meta",
            "content": "Has list metadata.",
            "metadata": [1, 2, 3],
        }
    ]
    result = gw.push_batch(events)
    assert result["status"] == "partial_error"
    assert result["inserted"] == 0
    assert len(result["errors"]) == 1
    assert "dict" in result["errors"][0]["error"]


def test_push_batch_non_dict_element(db, user_id):
    """Batch with non-dict elements (string, int, None) — clean error, no crash."""
    gw = IngestGateway(db, user_id)
    events = [
        {"source": "test", "event_type": "note", "title": "Good", "content": "Valid event."},
        "not-a-dict",
        42,
        None,
    ]
    result = gw.push_batch(events)
    assert result["status"] == "partial_error"
    assert result["inserted"] == 1
    assert len(result["errors"]) == 3
    for err in result["errors"]:
        assert "dict" in err["error"]


def test_push_batch_partial_errors(db, user_id):
    """Batch with mixed good and bad events — partial success."""
    gw = IngestGateway(db, user_id)
    events = [
        {"source": "test", "event_type": "note", "title": "Good 1", "content": "Valid event."},
        {"source": "test", "event_type": "note", "title": "Bad meta", "content": "Bad.", "metadata": [1, 2]},
        {"source": "test", "event_type": "note", "title": "Good 2", "content": "Another valid event."},
    ]
    result = gw.push_batch(events)
    assert result["status"] == "partial_error"
    assert result["inserted"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1
    assert result["total"] == 3


def test_push_event_no_timestamp_stores_utc(db, user_id):
    """Events pushed without a timestamp should default to UTC-aware datetime.

    Regression test: gateway.py previously used datetime.now() (naive local)
    which got mislabeled as UTC by require_utc(). Now uses datetime.now(timezone.utc).
    """
    gw = IngestGateway(db, user_id)
    result = gw.push(
        source="test",
        event_type="note",
        title="No timestamp",
        content="Event without explicit timestamp.",
    )
    assert result["status"] == "ok"

    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    ts_str = events[0]["timestamp"]
    ts = datetime.fromisoformat(ts_str)
    # Must be timezone-aware (has tzinfo)
    assert ts.tzinfo is not None or "+" in ts_str or "Z" in ts_str, (
        f"Stored timestamp '{ts_str}' appears to be naive (no timezone info). "
        f"Gateway should use datetime.now(timezone.utc)."
    )


def test_push_event_with_utc_timestamp_roundtrips(db, user_id):
    """UTC timestamp pushed via gateway should round-trip through DB correctly."""
    gw = IngestGateway(db, user_id)
    original_ts = "2026-06-15T12:00:00+00:00"
    result = gw.push(
        source="test",
        event_type="note",
        title="UTC event",
        content="Event with explicit UTC timestamp.",
        timestamp=original_ts,
    )
    assert result["status"] == "ok"

    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    stored_ts = datetime.fromisoformat(events[0]["timestamp"])
    expected_ts = datetime.fromisoformat(original_ts)
    # Should represent the same instant
    assert stored_ts.replace(tzinfo=timezone.utc) == expected_ts or stored_ts == expected_ts


def test_push_event_with_offset_timestamp_preserved(db, user_id):
    """Non-UTC offset timestamp should be stored as-is (fromisoformat preserves it)."""
    gw = IngestGateway(db, user_id)
    # Tokyo time (UTC+9)
    result = gw.push(
        source="test",
        event_type="note",
        title="Tokyo event",
        content="Event from Tokyo timezone.",
        timestamp="2026-06-15T21:00:00+09:00",
    )
    assert result["status"] == "ok"

    events = db.get_events(user_id, source="test")
    assert len(events) == 1
    stored_ts = datetime.fromisoformat(events[0]["timestamp"])
    # When normalized to UTC, should be 12:00 UTC
    from syke.time import require_utc
    utc_ts = require_utc(stored_ts)
    assert utc_ts.hour == 12
