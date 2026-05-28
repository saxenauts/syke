"""Tests for initialize_workspace() — workspace bootstrap."""

from __future__ import annotations

from pathlib import Path

from syke.runtime import workspace


def _patch_workspace(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(workspace, "SESSIONS_DIR", root / "sessions")
    monkeypatch.setattr(workspace, "SYKE_DB", root / "syke.db")
    monkeypatch.setattr(workspace, "MEMEX_PATH", root / "MEMEX.md")


def test_creates_workspace_dir(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ws"
    _patch_workspace(monkeypatch, root)
    workspace.initialize_workspace()
    assert root.is_dir()


def test_creates_sessions_dir(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ws"
    _patch_workspace(monkeypatch, root)
    workspace.initialize_workspace()
    assert (root / "sessions").is_dir()


def test_creates_adapters_dir(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ws"
    _patch_workspace(monkeypatch, root)
    workspace.initialize_workspace()
    assert (root / "adapters").is_dir()


def test_writes_psyche_md(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ws"
    _patch_workspace(monkeypatch, root)
    workspace.initialize_workspace()
    psyche = root / "PSYCHE.md"
    assert psyche.exists()
    assert "You are Syke" in psyche.read_text(encoding="utf-8")


def test_installs_adapter_markdowns(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ws"
    _patch_workspace(monkeypatch, root)
    workspace.initialize_workspace()
    adapters = list((root / "adapters").glob("*.md"))
    # At least some adapter markdowns should be installed from seeds
    assert len(adapters) > 0


def test_idempotent(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "ws"
    _patch_workspace(monkeypatch, root)
    workspace.initialize_workspace()
    workspace.initialize_workspace()  # second call — no error
    assert root.is_dir()
    assert (root / "PSYCHE.md").exists()
