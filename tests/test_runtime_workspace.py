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


def test_prepare_workspace_resets_agent_artifacts_when_source_db_changes(
    tmp_path: Path, monkeypatch
) -> None:
    workspace_root = tmp_path / "workspace"
    source_a = tmp_path / "source-a.db"
    source_b = tmp_path / "source-b.db"
    events_db = workspace_root / "events.db"
    agent_db = workspace_root / "agent.db"
    memex_path = workspace_root / "memex.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    _seed_source_db(source_a, "evt-a")
    _seed_source_db(source_b, "evt-b")

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "AGENT_DB", agent_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    workspace.prepare_workspace("user", source_db_path=source_a)
    agent_db.write_text("agent-state", encoding="utf-8")
    memex_path.write_text("stale memex", encoding="utf-8")
    (workspace_root / "scripts" / "helper.py").parent.mkdir(parents=True, exist_ok=True)
    (workspace_root / "scripts" / "helper.py").write_text("print('hi')", encoding="utf-8")
    (sessions_dir / "keep.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "keep.jsonl").write_text("{}", encoding="utf-8")

    workspace.prepare_workspace("user", source_db_path=source_b)

    assert agent_db.exists()
    assert agent_db.read_text(encoding="utf-8") == ""
    assert not memex_path.exists()
    assert not (workspace_root / "scripts" / "helper.py").exists()
    assert (sessions_dir / "keep.jsonl").exists()


def test_prepare_workspace_writes_agents_md(tmp_path: Path, monkeypatch) -> None:
    workspace_root = tmp_path / "workspace"
    source_db = tmp_path / "source.db"
    events_db = workspace_root / "events.db"
    agent_db = workspace_root / "agent.db"
    memex_path = workspace_root / "memex.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    _seed_source_db(source_db, "evt-1")

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "AGENT_DB", agent_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    workspace.prepare_workspace("user", source_db_path=source_db)

    agents_md = workspace_root / "AGENTS.md"
    assert agents_md.exists()
    assert "This is your persistent workspace." in agents_md.read_text(encoding="utf-8")
