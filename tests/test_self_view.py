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
        "## Graph",
        "## Recent runs",
        "- No wake has completed yet.",
        "memories_fts.memory_id = memories.id",
        "keep their id and created_at",
        "two existing memory ids",
        "Resolve a shortened ID to the full ID",
        "is rejected; one already over may shrink or hold, not grow.",
        f"- Writable: {workspace.resolve()} (workspace)",
    ):
        assert value in view
    for removed in (
        "As of:",
        "Tools:",
        "Time limit",
        "Operation ID",
        "Pi adds the host date",
        "soft target (",
        "Self-view size",
        "SQLite pages",
        "compress, split",
    ):
        assert removed not in view
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
    assert "- Last completed wake: cycle-accepted" in failed_view
    assert "- Latest wake cycle-failed ended failed at" in failed_view
    assert ": semantic gate failed." in failed_view
    assert "session-accepted.jsonl" in failed_view
    assert "session-failed.jsonl" in failed_view
    assert "Needs attention: latest wake ended failed." in failed_view

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
    assert "- Latest ask ask-1 at" in ask_view
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

    assert "- Latest ask ask-bad at" in view
    assert "failed: xxx" in view
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

    assert "- Now: 2 memories, 0 links. Unlinked: `indexed`, `missing-search`." in view
    assert "- Missing from search: 1 memory: `missing-search`." in view
    assert "artifacts/ 4.0 GiB" in view
    assert "- Workspace is 4.0 GiB, over its 3.0 GiB soft target." in view
    assert "Nothing is deleted automatically." in view
    assert "workspace over soft target" in view
    assert oversized_artifact.exists()
    db.close()


def test_self_view_names_memories_over_budget(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    _add_memex(db)
    _add_memory(db, "small", "Small memory")
    _add_memory(db, "large", " x" * 2_500)

    view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)

    assert "- Over budget: 1 of 2 memories exceeds 2,000 tokens: `large` 2,500." in view
    assert "Needs attention: 1 memory over budget." in view
    db.close()


def test_self_view_states_operating_notes_size_and_run_pointers(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    _add_memex(db)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "OPERATING.md").write_text("- keep one lesson\n", encoding="utf-8")
    run_folder = sessions.parent / "runtime" / "cycles" / "cycle-now"

    view = build_self_view(
        db,
        USER_ID,
        workspace_root=workspace,
        session_dir=sessions,
        cycle_runtime=run_folder,
    )

    from syke.memory.learned import measure_learned_projection

    notes = workspace.resolve() / "OPERATING.md"
    tokens = measure_learned_projection(notes.read_text(encoding="utf-8"))["tokens"]
    assert (
        f"- Operating notes: {notes}, {tokens:,} / 6,000 tokens. The prompt shows the whole "
        "file up to 6,000; past that, only the start. It is yours to prune."
    ) in view
    assert view.count("OPERATING.md") == 1
    assert f"- This run's folder: {run_folder.resolve()} (empty now;" in view
    assert f"newest file in {sessions.resolve()}." in view
    db.close()


def test_self_view_off_limits_paths_come_from_the_sandbox_profile(tmp_path: Path) -> None:
    from syke.runtime.sandbox import _credential_deny_paths
    from syke.runtime.self_view import _home_relative

    db, workspace, sessions = _open_state(tmp_path)
    view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)

    no_access = next(line for line in view.splitlines() if line.startswith("- No access: "))
    for path in _credential_deny_paths():
        assert _home_relative(path) in no_access
    db.close()


def test_self_view_names_a_failed_runs_surviving_folder(tmp_path: Path) -> None:
    db, workspace, sessions = _open_state(tmp_path)
    write_receipt(
        sessions.parent,
        {
            "id": "cycle-timeout",
            "started_at": "2026-07-30T11:00:00+00:00",
            "completed_at": "2026-07-30T11:10:00+00:00",
            "status": "failed",
            "acknowledged_record_ids": [],
            "memex_updated": False,
            "error": "Pi did not complete within 600.0s",
            "recovery": {"restored": True, "recovery_point": "rp-1"},
        },
    )
    folder = sessions.parent / "runtime" / "cycles" / "cycle-timeout"
    folder.mkdir(parents=True)
    (folder / "staged.py").write_text("print('apply')\n", encoding="utf-8")

    view = build_self_view(db, USER_ID, workspace_root=workspace, session_dir=sessions)

    assert "Pi did not complete within 600.0s." in view
    assert "Its graph and MEMEX changes were rolled back." in view
    assert f"Its run folder: {folder.resolve()} (1 file)." in view
    assert str(sessions.parent / "receipts" / "cycle-timeout.json") in view
    db.close()
