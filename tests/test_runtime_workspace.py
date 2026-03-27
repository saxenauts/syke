from __future__ import annotations

import sqlite3
from pathlib import Path

from syke.runtime import workspace


def _seed_source_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, user_id TEXT)")
    conn.execute("DELETE FROM events")
    conn.execute("INSERT INTO events (id, user_id) VALUES (?, ?)", (value, "test"))
    conn.commit()
    conn.close()


def test_refresh_events_db_skips_when_source_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    source_db = tmp_path / "source.db"
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(source_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    first = workspace.refresh_events_db(source_db)
    second = workspace.refresh_events_db(source_db)

    assert first["refreshed"] is True
    assert first["reason"] == "refreshed"
    assert second["refreshed"] is False
    assert second["reason"] == "unchanged"
    assert events_db.exists()


def test_refresh_events_db_refreshes_again_after_source_change(tmp_path: Path, monkeypatch) -> None:
    source_db = tmp_path / "source.db"
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(source_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    first = workspace.refresh_events_db(source_db)
    _seed_source_db(source_db, "evt-2")
    second = workspace.refresh_events_db(source_db)

    assert first["refreshed"] is True
    assert second["refreshed"] is True
    assert second["reason"] == "refreshed"
