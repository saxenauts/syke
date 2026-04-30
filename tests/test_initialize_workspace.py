"""Tests for initialize_workspace() — workspace bootstrap."""

from __future__ import annotations

from pathlib import Path

from syke.runtime import workspace


def test_creates_workspace_dir(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workspace.set_workspace_root(root)
    workspace.initialize_workspace()
    assert root.is_dir()


def test_creates_sessions_dir(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workspace.set_workspace_root(root)
    workspace.initialize_workspace()
    assert (root / "sessions").is_dir()


def test_creates_adapters_dir(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workspace.set_workspace_root(root)
    workspace.initialize_workspace()
    assert (root / "adapters").is_dir()


def test_writes_psyche_md(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workspace.set_workspace_root(root)
    workspace.initialize_workspace()
    psyche = root / "PSYCHE.md"
    assert psyche.exists()
    assert "You are Syke" in psyche.read_text(encoding="utf-8")


def test_installs_adapter_markdowns(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workspace.set_workspace_root(root)
    workspace.initialize_workspace()
    adapters = list((root / "adapters").glob("*.md"))
    # At least some adapter markdowns should be installed from seeds
    assert len(adapters) > 0


def test_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    workspace.set_workspace_root(root)
    workspace.initialize_workspace()
    workspace.initialize_workspace()  # second call — no error
    assert root.is_dir()
    assert (root / "PSYCHE.md").exists()
