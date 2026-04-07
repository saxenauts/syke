"""Single-store contract: events and memories coexist in one database."""

from __future__ import annotations

from datetime import datetime

from syke.db import SykeDB
from syke.models import Event, Memory


def test_events_and_memories_coexist_in_single_db(tmp_path) -> None:
    syke_db_path = tmp_path / "syke.db"

    with SykeDB(syke_db_path) as db:
        assert db.event_db_path == str(syke_db_path)

        db.insert_event(
            Event(
                user_id="u1",
                source="test",
                timestamp=datetime(2026, 4, 7, 12, 0, 0),
                event_type="note",
                content="test event",
            )
        )
        db.insert_memory(
            Memory(
                id="mem-1",
                user_id="u1",
                content="test memory",
                source_event_ids=[],
            )
        )

        assert db.count_events("u1") == 1
        assert db.count_memories("u1") == 1

    # No sibling events.db should be created
    assert not (tmp_path / "events.db").exists()
