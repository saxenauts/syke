from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from syke.db import SykeDB
from syke.models import Event
from syke.sense.writer import SenseWriter


def _make_event(i: int, *, external_id: str | None = None) -> Event:
    return Event(
        user_id="",
        source="sense-test",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=i),
        event_type="turn",
        title=f"event-{i}",
        content=f"content-{i}",
        external_id=external_id,
    )


def test_callback_fires_on_insert(db: SykeDB, user_id: str) -> None:
    """Test that registered callback is invoked when events are inserted."""
    writer = SenseWriter(db, user_id, flush_interval_s=1.0, max_batch_size=100)

    received_events: list[list[Event]] = []

    def on_insert(events: list[Event]) -> None:
        received_events.append(events)

    writer.add_on_insert_callback(on_insert)
    writer.start()

    # Enqueue and flush a single event
    writer.enqueue(_make_event(1))
    writer.stop()

    # Verify callback was called with the inserted event
    assert len(received_events) > 0
    assert len(received_events[0]) == 1
    assert received_events[0][0].title == "event-1"


def test_multiple_callbacks(db: SykeDB, user_id: str) -> None:
    """Test that multiple callbacks are all invoked on insert."""
    writer = SenseWriter(db, user_id, flush_interval_s=1.0, max_batch_size=100)

    call_count_1 = [0]
    call_count_2 = [0]

    def callback_1(events: list[Event]) -> None:
        call_count_1[0] += 1

    def callback_2(events: list[Event]) -> None:
        call_count_2[0] += 1

    writer.add_on_insert_callback(callback_1)
    writer.add_on_insert_callback(callback_2)
    writer.start()

    # Enqueue events to trigger flush
    for i in range(5):
        writer.enqueue(_make_event(i))
    writer.stop()

    # Both callbacks should have been called
    assert call_count_1[0] > 0
    assert call_count_2[0] > 0
