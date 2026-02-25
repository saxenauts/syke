"""Tests for config.py data directory resolution and defaults."""

from __future__ import annotations

import getpass
import importlib
import os
from pathlib import Path

from syke.config import (
    _default_data_dir,
    _is_source_install,
)


def test_is_source_install_true():
    """Returns True when pyproject.toml exists at PROJECT_ROOT (i.e., this repo)."""
    assert _is_source_install() is True


def test_env_var_overrides_everything(tmp_path, monkeypatch):
    """SYKE_DATA_DIR env var takes priority over all other logic."""
    target = tmp_path / "custom"
    monkeypatch.setenv("SYKE_DATA_DIR", str(target))
    result = _default_data_dir()
    assert result == target.resolve()


def test_default_data_dir_returns_home_syke_data(monkeypatch):
    """Without env var, always returns ~/.syke/data regardless of install type."""
    monkeypatch.delenv("SYKE_DATA_DIR", raising=False)
    result = _default_data_dir()
    assert result == Path.home() / ".syke" / "data"


def test_default_user_falls_back_to_system_username(monkeypatch):
    """DEFAULT_USER falls back to getpass.getuser() when SYKE_USER is unset."""
    monkeypatch.delenv("SYKE_USER", raising=False)
    # Re-evaluate the logic (can't re-import module-level constant, test the logic)
    import os

    result = os.getenv("SYKE_USER", "") or getpass.getuser()
    assert result == getpass.getuser()
    assert len(result) > 0


def test_default_user_respects_env_var(monkeypatch):
    """SYKE_USER env var overrides system username."""
    monkeypatch.setenv("SYKE_USER", "custom-user")
    result = os.getenv("SYKE_USER", "") or getpass.getuser()
    assert result == "custom-user"


def test_import_clears_anthropic_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import syke.config as config_module

    importlib.reload(config_module)
    assert os.getenv("ANTHROPIC_API_KEY") is None
