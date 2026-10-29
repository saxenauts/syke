"""Current workspace bootstrap contracts."""

from __future__ import annotations

from pathlib import Path

from syke import config
from syke.runtime import workspace


def _patch_workspace(monkeypatch, root: Path) -> None:
    control = root.parent / "control"
    monkeypatch.setenv("SYKE_WORKSPACE_ROOT", str(root))
    monkeypatch.setenv("SYKE_CONTROL_ROOT", str(control))
    monkeypatch.setattr(workspace, "SYKE_ROOT", root.parent)
    monkeypatch.setattr(workspace, "CONTROL_ROOT", control)
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(workspace, "RUNTIME_DIR", control / "runtime")
    monkeypatch.setattr(workspace, "TEMP_DIR", control / "runtime" / "tmp")
    monkeypatch.setattr(workspace, "TOKENIZER_CACHE_DIR", control / "tokenizers")
    monkeypatch.setattr(workspace, "SESSIONS_DIR", control / "sessions")
    monkeypatch.setattr(workspace, "RECEIPTS_DIR", control / "receipts")
    monkeypatch.setattr(workspace, "RECORDS_DIR", control / "records")
    monkeypatch.setattr(workspace, "SYKE_DB", root / "syke.db")
    monkeypatch.setattr(workspace, "MEMEX_PATH", root / "MEMEX.md")
    monkeypatch.setattr(workspace, "ARTIFACTS_DIR", root / "artifacts")
    monkeypatch.setattr(workspace, "HARNESS_DIR", root / "harness")
    monkeypatch.setattr(workspace, "SCRATCH_DIR", root / "scratch")


def test_initialize_workspace_creates_only_the_current_layout(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    _patch_workspace(monkeypatch, root)

    workspace.initialize_workspace()
    workspace.initialize_workspace()

    control = tmp_path / "control"
    for path in (
        control / "sessions",
        control / "receipts",
        control / "records",
        control / "runtime" / "tmp",
        control / "tokenizers",
        root / "artifacts",
        root / "harness",
        root / "scratch",
    ):
        assert path.is_dir()
    assert list((root / "adapters").glob("*.md"))
    assert not (root / "syke.db").exists()
    assert not (root / "MEMEX.md").exists()


def test_user_syke_db_path_is_pure(tmp_path: Path, monkeypatch) -> None:
    syke_root = tmp_path / ".syke"
    monkeypatch.setattr(config, "SYKE_HOME", syke_root)
    monkeypatch.delenv("SYKE_DB", raising=False)
    monkeypatch.delenv("SYKE_WORKSPACE_ROOT", raising=False)

    assert config.user_syke_db_path("person") == syke_root / "workspace" / "syke.db"
    assert not syke_root.exists()
