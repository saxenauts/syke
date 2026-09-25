"""System contracts for Syke's bounded read-only self-view."""

from __future__ import annotations

import json
from pathlib import Path

from syke import __version__
from syke.control import write_receipt
from syke.db import SykeDB
from syke.memory.memex import update_memex
from syke.memory.memex_budget import warm_memex_tokenizer
from syke.runtime.self_view import build_self_view

USER_ID = "one-person"


def _open_state(tmp_path: Path) -> tuple[SykeDB, Path, Path]:
    workspace = tmp_path / "workspace"
    sessions = tmp_path / "control" / "sessions"
    return SykeDB(workspace / "syke.db", user_id=USER_ID), workspace, sessions


def _add_memex(db: SykeDB) -> str:
    return update_memex(db, USER_ID, "Current map")


def _add_memory(db: SykeDB, memory_id: str, content: str) -> None:
    db.conn.execute(
        """INSERT INTO memories
           (id, user_id, content, created_at, updated_at)
           VALUES (?, ?, ?, '2026-07-29', NULL)""",
        (memory_id, USER_ID, content),
    )
    db.conn.commit()


def _add_cycle(
    sessions: Path,
    cycle_id: str,
    *,
    started_at: str,
    completed_at: str,
    status: str,
    session_id: str,
    error: str | None = None,
) -> None:
    write_receipt(
        sessions.parent,
        {
            "id": cycle_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "status": status,
            "session_id": session_id,
            "acknowledged_record_ids": [],
            "memex_updated": status == "completed",
            "error": error,
        },
    )


def _write_session(
    sessions: Path,
    *,
    session_id: str,
    name: str,
    started_at: str,
    completed_at: str,
    error: str | None = None,
) -> Path:
    path = sessions / f"{started_at.replace(':', '-')}_{session_id}.jsonl"
    entries = [
        {
            "type": "session",
            "version": 3,
            "id": session_id,
            "timestamp": started_at,
            "cwd": str(sessions.parent.parent / "workspace"),
        },
        {
            "type": "session_info",
            "id": f"name-{session_id}",
            "timestamp": started_at,
            "name": name,
        },
        {
            "type": "message",
            "id": f"assistant-{session_id}",
            "timestamp": completed_at,
            "message": {
                "role": "assistant",
                "provider": "openai-codex",
                "model": "gpt-5.5",
                "content": [{"type": "text", "text": "Operation complete."}],
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 10,
                    "cacheWrite": 0,
                    "cost": {"total": 0.02},
                },
                "stopReason": "error" if error else "stop",
                "errorMessage": error,
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )
    return path


def test_self_view_exposes_authority_without_mutating_state(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    memex_id = _add_memex(db)
    warm_memex_tokenizer()
    graph_changes = db.conn.total_changes
    files_before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    view = build_self_view(
        db,
        USER_ID,
        workspace_root=workspace,
        session_dir=sessions,
    )

    assert view.startswith("# Self-observation")
    for value in (
        f"Syke {__version__}",
        str(workspace / "syke.db"),
        str(workspace),
        str(sessions.parent / "receipts"),
        str(sessions.parent / "records"),
        str(sessions),
        memex_id,
        "# Graph and search contract",
        "# Accepted continuation",
    ):
        assert value in view
    assert len(view) < 12_000
    assert db.conn.total_changes == graph_changes
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == files_before
    db.close()


def test_self_view_distinguishes_accepted_failed_and_ask_operations(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    _add_cycle(
        sessions,
        "cycle-accepted",
        started_at="2026-07-30T10:00:00+00:00",
        completed_at="2026-07-30T10:05:00+00:00",
        status="completed",
        session_id="session-accepted",
    )
    _write_session(
        sessions,
        session_id="session-accepted",
        name="syke:synthesis:cycle-accepted",
        started_at="2026-07-30T10:00:00+00:00",
        completed_at="2026-07-30T10:05:00+00:00",
    )
    _add_cycle(
        sessions,
        "cycle-failed",
        started_at="2026-07-30T11:00:00+00:00",
        completed_at="2026-07-30T11:04:00+00:00",
        status="failed",
        session_id="session-failed",
        error="semantic gate failed",
    )
    _write_session(
        sessions,
        session_id="session-failed",
        name="syke:synthesis:cycle-failed",
        started_at="2026-07-30T11:00:00+00:00",
        completed_at="2026-07-30T11:04:00+00:00",
        error="semantic gate failed",
    )
    failed_view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)
    for value in (
        "cycle-accepted",
        "session-accepted",
        "cycle-failed",
        "session-failed",
    ):
        assert value in failed_view
    assert "## Accepted continuation" in failed_view
    assert "This operation is not the accepted continuation" in failed_view
    assert "semantic gate failed" in failed_view

    _write_session(
        sessions,
        session_id="session-ask",
        name="syke:ask:ask-1",
        started_at="2026-07-30T12:00:00+00:00",
        completed_at="2026-07-30T12:01:00+00:00",
    )
    ask_view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)
    assert "cycle-accepted" in ask_view
    assert "session-ask" in ask_view
    assert "ask-1" in ask_view
    db.close()


def test_self_view_bounds_errors_and_does_not_inventory_workspace(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    artifact = workspace / "harness" / "candidate.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("not installed", encoding="utf-8")
    _write_session(
        sessions,
        session_id="session-bad",
        name="syke:ask:ask-bad",
        started_at="2026-07-30T12:00:00+00:00",
        completed_at="2026-07-30T12:01:00+00:00",
        error="x" * 5000,
    )

    view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)

    assert "Recorded error:" in view
    assert "x" * 500 not in view
    assert "candidate.py" not in view
    assert len(view) < 12_000
    db.close()


def test_self_view_keeps_graph_and_workspace_pressure_visible(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    _add_memex(db)
    _add_memory(db, "indexed", "Indexed memory")
    _add_memory(db, "missing-search", "Missing search memory")
    db.conn.execute("DELETE FROM memories_fts WHERE memory_id = 'missing-search'")
    db.conn.commit()
    oversized_artifact = workspace / "artifacts" / "oversized.bin"
    oversized_artifact.parent.mkdir(parents=True)
    with oversized_artifact.open("wb") as handle:
        handle.truncate(4 * 1024 * 1024 * 1024)

    view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)

    assert "2 current memories" in view
    assert "missing from search" in view
    assert "Workspace soft target exceeded" in view
    assert "nothing is deleted automatically" in view
    assert oversized_artifact.exists()
    db.close()


def test_self_view_names_memories_over_budget(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    _add_memex(db)
    _add_memory(db, "small", "Small memory")
    _add_memory(db, "large", " x" * 2_500)

    view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)

    assert "Memory budget: 1 of 2 current memories exceeds 2,000 tokens: `large` 2,500." in view
    assert "1 current memory exceeds the memory budget" in view
    db.close()
