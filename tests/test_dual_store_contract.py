from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from syke.db import SykeDB
from syke.models import Event, Memory


def test_split_store_initializes_empty_canonical_events_store(tmp_path) -> None:
    syke_db_path = tmp_path / "syke.db"

    with SykeDB(syke_db_path) as db:
        assert db.event_db_path == str(tmp_path / "events.db")
        assert (tmp_path / "events.db").exists()
        assert db.count_events("u1") == 0
        assert db.count_memories("u1") == 0
        assert db.event_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_split_store_ignores_stale_events_in_syke_db(tmp_path) -> None:
    syke_db_path = tmp_path / "syke.db"
    events_db_path = tmp_path / "events.db"

    with sqlite3.connect(syke_db_path) as conn:
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
                ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            INSERT INTO events (id, user_id, source, timestamp, event_type, title, content)
            VALUES ('evt-legacy', 'u1', 'codex', '2026-03-27T12:00:00', 'turn', 'legacy', 'legacy event');
            """
        )

    with SykeDB(syke_db_path) as db:
        db.insert_memory(
            Memory(
                id="mem-1",
                user_id="u1",
                content="current memory",
                source_event_ids=["evt-live"],
            )
        )
        assert db.event_db_path == str(events_db_path)
        assert db.count_events("u1") == 0
        assert db.count_memories("u1") == 1

    with sqlite3.connect(events_db_path) as events_conn:
        assert events_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    with sqlite3.connect(syke_db_path) as syke_conn:
        assert syke_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert syke_conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", ("u1",)).fetchone()[0] == 1


def test_split_store_routes_event_writes_to_events_db_only(tmp_path) -> None:
    syke_db_path = tmp_path / "syke.db"

    with SykeDB(syke_db_path) as db:
        inserted = db.insert_event(
            Event(
                user_id="u1",
                source="github",
                timestamp=datetime(2026, 3, 27, 18, 0, 0),
                event_type="issue",
                content="new event",
            )
        )
        assert inserted is True

        db.insert_memory(
            Memory(
                id="mem-2",
                user_id="u1",
                content="new memory",
                source_event_ids=["evt-2"],
            )
        )

    with sqlite3.connect(tmp_path / "events.db") as events_conn:
        assert events_conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", ("u1",)).fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            events_conn.execute("SELECT COUNT(*) FROM memories").fetchone()

    with sqlite3.connect(syke_db_path) as syke_conn:
        with pytest.raises(sqlite3.OperationalError):
            syke_conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", ("u1",)).fetchone()
        with pytest.raises(sqlite3.OperationalError):
            syke_conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()
        assert syke_conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", ("u1",)).fetchone()[0] == 1
