from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from syke.db import SykeDB
from syke.runtime import workspace as workspace_module
from syke.llm.backends import pi_synthesis as pi_synthesis_module


def _load_memory_replay_module():
    module_path = Path(__file__).resolve().parents[1] / "memory_replay.py"
    spec = importlib.util.spec_from_file_location("memory_replay_for_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load memory_replay module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_workspace_contract_rejects_missing_canonical_db(tmp_path: Path) -> None:
    memory_replay = _load_memory_replay_module()

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    syke_db = workspace_root / "syke.db"

    with pytest.raises(RuntimeError, match="missing canonical DB"):
        memory_replay._validate_workspace_contract(
            workspace_root,
            syke_db,
        )


def test_snapshot_memex_exports_active_memories_and_links(tmp_path: Path) -> None:
    memory_replay = _load_memory_replay_module()

    replay_db = SykeDB(tmp_path / "replay.db")
    try:
        replay_db.conn.execute(
            """INSERT INTO memories
               (id, user_id, content, source_event_ids, created_at, active)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (
                "mem-1",
                "user",
                "Durable memory content",
                '["evt-1","evt-2"]',
                "2026-01-31T12:00:00Z",
            ),
        )
        replay_db.conn.execute(
            """INSERT INTO links
               (id, user_id, source_id, target_id, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "link-1",
                "user",
                "mem-1",
                "mem-2",
                "related topic",
                "2026-01-31T12:01:00Z",
            ),
        )
        replay_db.conn.commit()

        snapshot = memory_replay.snapshot_memex(
            replay_db,
            "user",
            "2026-01-31",
            1,
            {"status": "completed"},
        )

        assert snapshot["cursor"] is None
        assert snapshot["memories"][0]["id"] == "mem-1"
        assert snapshot["links"][0]["id"] == "link-1"
    finally:
        replay_db.close()


def test_persist_run_checkpoint_writes_run_status(tmp_path: Path) -> None:
    memory_replay = _load_memory_replay_module()

    payload = {
        "metadata": {
            "started_at": "2026-04-20T00:00:00+00:00",
            "completed_at": None,
            "heartbeat_at": "2026-04-20T00:01:00+00:00",
            "status": "running",
            "selected_replay_cycles": 12,
            "completed_cycles": 3,
            "phase": "cycle-3",
        },
        "timeline": [],
    }

    memory_replay._persist_run_checkpoint(tmp_path, payload)

    status = json.loads((tmp_path / "run_status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "replay"
    assert status["status"] == "running"
    assert status["total_units"] == 12
    assert status["completed_units"] == 3
    assert status["unit_label"] == "cycles"


def test_temporary_workspace_binding_restores_globals_and_env(
    tmp_path: Path, monkeypatch
) -> None:
    memory_replay = _load_memory_replay_module()

    original_workspace = workspace_module.WORKSPACE_ROOT
    original_sessions = workspace_module.SESSIONS_DIR
    original_pi_sessions = pi_synthesis_module.SESSIONS_DIR
    original_env_self_obs = "keep-self-obs"
    original_env_harness = "keep-harness"
    original_env_agent = "keep-agent-dir"
    monkeypatch.setenv("SYKE_DISABLE_SELF_OBSERVATION", original_env_self_obs)
    monkeypatch.setenv("SYKE_SANDBOX_HARNESS_PATHS", original_env_harness)
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", original_env_agent)

    stop_calls: list[str] = []

    monkeypatch.setattr("syke.runtime.stop_pi_runtime", lambda: stop_calls.append("stop"))

    temp_workspace = tmp_path / "workspace"
    temp_sessions = tmp_path / "sessions"
    temp_harness = tmp_path / "slice"
    temp_pi_agent = tmp_path / ".pi"

    with memory_replay.temporary_workspace_binding(
        temp_workspace,
        sessions_dir=temp_sessions,
        harness_paths=temp_harness,
        pi_agent_dir=temp_pi_agent,
    ):
        assert workspace_module.WORKSPACE_ROOT == temp_workspace
        assert workspace_module.SESSIONS_DIR == temp_sessions
        assert pi_synthesis_module.SESSIONS_DIR == temp_sessions
        assert os.environ["SYKE_DISABLE_SELF_OBSERVATION"] == "1"
        assert os.environ["SYKE_SANDBOX_HARNESS_PATHS"] == str(temp_harness)
        assert os.environ["SYKE_PI_AGENT_DIR"] == str(temp_pi_agent)

    assert workspace_module.WORKSPACE_ROOT == original_workspace
    assert workspace_module.SESSIONS_DIR == original_sessions
    assert pi_synthesis_module.SESSIONS_DIR == original_pi_sessions
    assert os.environ["SYKE_DISABLE_SELF_OBSERVATION"] == original_env_self_obs
    assert os.environ["SYKE_SANDBOX_HARNESS_PATHS"] == original_env_harness
    assert os.environ["SYKE_PI_AGENT_DIR"] == original_env_agent
    assert stop_calls == ["stop", "stop"]


def test_zero_prompt_targets_user_work_not_workspace_upkeep() -> None:
    memory_replay = _load_memory_replay_module()

    prompt = memory_replay.build_skill_override("zero")

    assert prompt is not None
    assert "active work threads" in prompt
    assert "workspace maintenance" in prompt
    assert "syke.db row counts" in prompt
    assert "Modify any part of the workspace to help future cycles." not in prompt


def test_prepare_replay_cycle_workspace_installs_shim_and_reference_file(
    tmp_path: Path,
) -> None:
    from datetime import datetime

    memory_replay = _load_memory_replay_module()

    simulated_now = datetime(2026, 3, 7, 23, 59)
    memory_replay._prepare_replay_cycle_workspace(tmp_path, simulated_now)

    # Tool surface: date shim exists and is executable
    date_shim = tmp_path / ".time-sandbox" / "bin" / "date"
    assert date_shim.exists()
    assert os.access(date_shim, os.X_OK)

    # Filesystem surface: REFERENCE_TIME.md carries the simulated cutoff
    ref_file = tmp_path / "REFERENCE_TIME.md"
    assert ref_file.exists()
    content = ref_file.read_text(encoding="utf-8")
    assert "2026-03-07 23:59" in content
    assert "2026-03-07T23:59:00" in content
    assert "Do not use wall-clock time" in content

    # Re-running with a later simulated_now overwrites (shim must advance per cycle)
    later_now = datetime(2026, 3, 8, 23, 59)
    memory_replay._prepare_replay_cycle_workspace(tmp_path, later_now)
    content2 = (tmp_path / "REFERENCE_TIME.md").read_text(encoding="utf-8")
    assert "2026-03-08 23:59" in content2
    assert "2026-03-07 23:59" not in content2
