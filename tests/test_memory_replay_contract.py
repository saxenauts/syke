from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest


def _load_memory_replay_module():
    module_path = Path(__file__).resolve().parents[1] / "experiments" / "memory_replay.py"
    spec = importlib.util.spec_from_file_location("memory_replay_for_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load memory_replay module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_replay_workspace_updates_workspace_bindings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_replay = _load_memory_replay_module()

    import syke.runtime as runtime_module
    from syke.llm.backends import pi_synthesis as pi_synthesis_module
    from syke.runtime import workspace as workspace_module

    monkeypatch.setattr(runtime_module, "stop_pi_runtime", lambda: None)
    monkeypatch.delenv("SYKE_REPLAY_WORKSPACE", raising=False)

    workspace_root, syke_db = memory_replay.configure_replay_workspace(tmp_path / "run")

    assert workspace_root == tmp_path / "run" / "workspace"
    assert syke_db == workspace_root / "syke.db"
    assert workspace_module.SYKE_DB == syke_db
    assert workspace_module.EVENTS_DB == workspace_root / "events.db"
    assert workspace_module.MEMEX_PATH == workspace_root / "MEMEX.md"
    assert pi_synthesis_module.SYKE_DB == syke_db
    assert "SYKE_REPLAY_WORKSPACE" not in os.environ


def test_restore_workspace_bindings_restores_prior_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory_replay = _load_memory_replay_module()

    import syke.runtime as runtime_module
    from syke.llm.backends import pi_synthesis as pi_synthesis_module
    from syke.runtime import workspace as workspace_module

    monkeypatch.setattr(runtime_module, "stop_pi_runtime", lambda: None)

    original_snapshot = memory_replay.capture_workspace_bindings()
    workspace_root, syke_db = memory_replay.configure_replay_workspace(tmp_path / "run")

    assert workspace_module.SYKE_DB == syke_db
    assert pi_synthesis_module.SYKE_DB == syke_db

    memory_replay.restore_workspace_bindings(original_snapshot)

    assert workspace_module.WORKSPACE_ROOT == original_snapshot["workspace"]["WORKSPACE_ROOT"]
    assert pi_synthesis_module.SYKE_DB == original_snapshot["pi_synthesis"]["SYKE_DB"]


def test_validate_workspace_contract_rejects_missing_canonical_db(tmp_path: Path) -> None:
    memory_replay = _load_memory_replay_module()

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    syke_db = workspace_root / "syke.db"

    with pytest.raises(RuntimeError, match="missing canonical DB"):
        memory_replay._validate_workspace_contract(
            workspace_root,
            syke_db,
            require_events_db=False,
        )


def test_validate_workspace_contract_requires_readonly_events_snapshot(tmp_path: Path) -> None:
    memory_replay = _load_memory_replay_module()

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    syke_db = workspace_root / "syke.db"
    syke_db.touch()

    events_db = workspace_root / "events.db"
    events_db.touch()
    os.chmod(events_db, stat.S_IRUSR | stat.S_IWUSR)

    with pytest.raises(RuntimeError, match="events.db is writable"):
        memory_replay._validate_workspace_contract(
            workspace_root,
            syke_db,
            require_events_db=True,
        )


def test_validate_workspace_contract_rejects_events_aliasing_syke_db(tmp_path: Path) -> None:
    memory_replay = _load_memory_replay_module()

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    syke_db = workspace_root / "syke.db"
    syke_db.touch()

    events_db = workspace_root / "events.db"
    events_db.symlink_to("syke.db")

    with pytest.raises(RuntimeError, match="events.db must not alias syke.db"):
        memory_replay._validate_workspace_contract(
            workspace_root,
            syke_db,
            require_events_db=True,
        )
