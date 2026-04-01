from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from syke.runtime import workspace


def _seed_source_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, user_id TEXT)")
    conn.execute("DELETE FROM events")
    conn.execute("INSERT INTO events (id, user_id) VALUES (?, ?)", (value, "test"))
    conn.commit()
    conn.close()


def _seed_mixed_store(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, user_id TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY)")
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


def test_refresh_events_db_skips_same_file_workspace_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(events_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    result = workspace.refresh_events_db(events_db)
    status = workspace.workspace_status()

    assert result["refreshed"] is False
    assert result["reason"] == "workspace_snapshot"
    assert result["source_db"] == str(events_db.resolve())
    assert status["events_db_source"] == str(events_db.resolve())


def test_refresh_events_db_ignores_wal_and_shm_churn_when_events_revision_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_db = tmp_path / "source.db"
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(source_db, "evt-1")
    (tmp_path / "source.db-wal").write_text("wal-1", encoding="utf-8")
    (tmp_path / "source.db-shm").write_text("shm-1", encoding="utf-8")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    first = workspace.refresh_events_db(source_db)
    (tmp_path / "source.db-wal").write_text("wal-2", encoding="utf-8")
    (tmp_path / "source.db-shm").write_text("shm-2", encoding="utf-8")
    second = workspace.refresh_events_db(source_db)

    assert first["refreshed"] is True
    assert second["refreshed"] is False
    assert second["reason"] == "unchanged"


def test_refresh_events_db_repairs_invalid_snapshot_even_when_source_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_db = tmp_path / "source.db"
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(source_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    first = workspace.refresh_events_db(source_db)
    os.chmod(events_db, 0o644)
    events_db.write_bytes(b"")

    second = workspace.refresh_events_db(source_db)

    assert first["refreshed"] is True
    assert second["refreshed"] is True
    assert second["reason"] == "refreshed"
    assert events_db.stat().st_size > 0
    assert not os.access(events_db, os.W_OK)


def test_refresh_events_db_skips_when_source_is_existing_workspace_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(events_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    result = workspace.refresh_events_db(events_db)

    assert result["refreshed"] is False
    assert result["reason"] == "workspace_snapshot"
    assert result["source_db"] == str(events_db.resolve())
    assert result["source_size_bytes"] == result["dest_size_bytes"]
    status = workspace.workspace_status()
    assert status["events_db_source"] == str(events_db.resolve())


def test_prepare_workspace_binds_to_exact_canonical_syke_db_and_resets_on_binding_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    source_a = tmp_path / "source-a.db"
    source_b = tmp_path / "source-b.db"
    canonical_a = tmp_path / "stores" / "a" / "syke.db"
    canonical_b = tmp_path / "stores" / "b" / "syke.db"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    _seed_source_db(source_a, "evt-a")
    _seed_source_db(source_b, "evt-b")

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "SYKE_DB", syke_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    workspace.prepare_workspace("user", source_db_path=source_a, syke_db_path=canonical_a)
    assert syke_db.exists()
    assert syke_db.samefile(canonical_a)

    memex_path.write_text("stale memex", encoding="utf-8")
    (workspace_root / "scripts" / "helper.py").parent.mkdir(parents=True, exist_ok=True)
    (workspace_root / "scripts" / "helper.py").write_text("print('hi')", encoding="utf-8")
    (sessions_dir / "keep.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "keep.jsonl").write_text("{}", encoding="utf-8")

    workspace.prepare_workspace("user", source_db_path=source_b, syke_db_path=canonical_b)

    assert syke_db.samefile(canonical_b)
    assert canonical_b.exists()
    assert not memex_path.exists()
    assert not (workspace_root / "scripts" / "helper.py").exists()
    assert not (sessions_dir / "keep.jsonl").exists()


def test_prepare_workspace_writes_agents_md_and_records_binding_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    source_db = tmp_path / "source.db"
    canonical_db = tmp_path / "store" / "syke.db"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    _seed_source_db(source_db, "evt-1")

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "SYKE_DB", syke_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    result = workspace.prepare_workspace(
        "user", source_db_path=source_db, syke_db_path=canonical_db
    )

    agents_md = workspace_root / "AGENTS.md"
    assert agents_md.exists()
    assert syke_db.samefile(canonical_db)
    assert result["syke_db_created"] is True
    assert "`events.db` is read-only evidence." in agents_md.read_text(encoding="utf-8")
    status = workspace.workspace_status()
    assert status["syke_db_target"] == str(canonical_db.resolve())
    assert status["events_db_source"] == str(source_db.resolve())
    assert status["agents_md_exists"] is True


def test_prepare_workspace_rejects_canonical_syke_db_as_events_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    canonical_db = tmp_path / "store" / "syke.db"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    canonical_db.parent.mkdir(parents=True, exist_ok=True)
    canonical_db.touch()

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "SYKE_DB", syke_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    with pytest.raises(ValueError, match="canonical syke\\.db"):
        workspace.prepare_workspace(
            "user",
            source_db_path=canonical_db,
            syke_db_path=canonical_db,
        )


def test_refresh_events_db_rejects_source_with_learned_state_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_db = tmp_path / "source.db"
    events_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_mixed_store(source_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    with pytest.raises(ValueError, match="learned-state tables"):
        workspace.refresh_events_db(source_db)


def test_refresh_events_db_skips_copy_when_source_is_current_workspace_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_db = tmp_path / "workspace-events.db"
    state_file = tmp_path / "workspace-state.json"

    _seed_source_db(source_db, "evt-1")

    monkeypatch.setattr(workspace, "EVENTS_DB", source_db)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)

    result = workspace.refresh_events_db(source_db)

    assert result["refreshed"] is False
    assert result["reason"] == "workspace_snapshot"
    assert result["source_db"] == str(source_db.resolve())
    assert result["source_size_bytes"] == source_db.stat().st_size

    status = workspace.workspace_status()
    assert status["events_db_source"] == str(source_db.resolve())
    assert status["events_db_size"] == source_db.stat().st_size


def test_validate_workspace_does_not_require_agents_md(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    workspace_root.mkdir()
    conn = sqlite3.connect(events_db)
    conn.execute("CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT)")
    conn.commit()
    conn.close()
    os.chmod(events_db, 0o444)
    syke_db.touch()
    sessions_dir.mkdir()
    for subdir in workspace.WORKSPACE_DIRS:
        (workspace_root / subdir).mkdir()

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "SYKE_DB", syke_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    validation = workspace.validate_workspace()

    assert validation["valid"] is True
    assert "AGENTS.md missing" not in validation["issues"]


def test_validate_workspace_rejects_snapshot_with_learned_state_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"

    workspace_root.mkdir()
    _seed_mixed_store(events_db, "evt-1")
    os.chmod(events_db, 0o444)
    syke_db.touch()
    sessions_dir.mkdir()
    for subdir in workspace.WORKSPACE_DIRS:
        (workspace_root / subdir).mkdir()

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "SYKE_DB", syke_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)

    validation = workspace.validate_workspace()

    assert validation["valid"] is False
    assert "events.db is not a valid read-only events snapshot" in validation["issues"]


def test_prepare_workspace_stops_runtime_when_db_binding_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    source_a = tmp_path / "source-a.db"
    source_b = tmp_path / "source-b.db"
    canonical_a = tmp_path / "stores" / "a" / "syke.db"
    canonical_b = tmp_path / "stores" / "b" / "syke.db"
    events_db = workspace_root / "events.db"
    syke_db = workspace_root / "syke.db"
    memex_path = workspace_root / "MEMEX.md"
    state_file = workspace_root / ".workspace_state.json"
    sessions_dir = workspace_root / "sessions"
    stop_runtime = Mock()

    _seed_source_db(source_a, "evt-a")
    _seed_source_db(source_b, "evt-b")

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "EVENTS_DB", events_db)
    monkeypatch.setattr(workspace, "SYKE_DB", syke_db)
    monkeypatch.setattr(workspace, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(workspace, "WORKSPACE_STATE", state_file)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr("syke.runtime.stop_pi_runtime", stop_runtime)

    workspace.prepare_workspace("user", source_db_path=source_a, syke_db_path=canonical_a)
    stop_runtime.assert_not_called()

    workspace.prepare_workspace("user", source_db_path=source_b, syke_db_path=canonical_b)
    stop_runtime.assert_called_once_with()
