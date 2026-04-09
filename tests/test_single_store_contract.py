"""Single-store contract: memories live in one database, no events table."""

from __future__ import annotations

from syke.db import SykeDB
from syke.models import Memory


def test_memories_in_single_db(tmp_path) -> None:
    syke_db_path = tmp_path / "syke.db"

    with SykeDB(syke_db_path) as db:
        db.insert_memory(
            Memory(
                id="mem-1",
                user_id="u1",
                content="test memory",
                source_event_ids=[],
            )
        )

        assert db.count_memories("u1") == 1

        # No events table should exist
        tables = [
            r[0]
            for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "events" not in tables
        assert "ingestion_runs" not in tables
