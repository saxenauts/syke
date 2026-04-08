"""Tests for DB schema migrations — source_instance_id column."""

from __future__ import annotations

from datetime import datetime

import pytest

from syke.db import SykeDB
from syke.models import Event


@pytest.fixture
def db() -> SykeDB:
    return SykeDB(":memory:")


def test_source_instance_id_column_exists(db: SykeDB) -> None:
    rows = db.conn.execute("PRAGMA table_info(events)").fetchall()
    columns = [row[1] for row in rows]
    assert "source_instance_id" in columns


def test_event_with_source_instance_id(db: SykeDB) -> None:
    event = Event(
        user_id="u1",
        source="test",
        timestamp=datetime(2025, 1, 1, 12, 0),
        event_type="test",
        content="hello",
        source_instance_id="instance-abc",
    )
    inserted = db.insert_event(event)
    assert inserted is True
    assert db.count_events("u1") == 1
