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


def test_writer_batches_inserts(db: SykeDB, user_id: str) -> None:
    writer = SenseWriter(db, user_id, flush_interval_s=1.0, max_batch_size=100)

    writer.start()
    for i in range(200):
        writer.enqueue(_make_event(i))
    writer.stop()

    assert db.count_events(user_id, "sense-test") == 200
    assert writer.flush_count <= 5


def test_writer_deduplicates(db: SykeDB, user_id: str) -> None:
    writer = SenseWriter(db, user_id, flush_interval_s=1.0, max_batch_size=100)
    writer.start()
    writer.enqueue(_make_event(1, external_id="dup-1"))
    writer.enqueue(_make_event(2, external_id="dup-1"))
    writer.stop()

    count = cast(
        int,
        db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE user_id = ? AND source = ? AND external_id = ?",
            (user_id, "sense-test", "dup-1"),
        ).fetchone()[0],
    )
    assert count == 1


def test_writer_drains_on_stop(db: SykeDB, user_id: str) -> None:
    writer = SenseWriter(db, user_id, flush_interval_s=10.0, max_batch_size=100)
    writer.start()
    for i in range(50):
        writer.enqueue(_make_event(i))
    writer.stop()

    assert db.count_events(user_id, "sense-test") == 50
