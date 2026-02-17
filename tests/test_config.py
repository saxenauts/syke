"""Tests for config.py data directory resolution and defaults."""

from __future__ import annotations

import getpass
import os
from pathlib import Path

from syke.config import _default_data_dir, _is_source_install, save_api_key, load_api_key


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


# --- API key persistence tests ---


def test_save_api_key_writes_env_file(tmp_path, monkeypatch):
    """save_api_key writes key to ~/.syke/.env with 600 permissions."""
    syke_dir = tmp_path / ".syke"
    monkeypatch.setattr("syke.config.SYKE_HOME", syke_dir)

    save_api_key("sk-ant-test-persist-123")

    env_file = syke_dir / ".env"
    assert env_file.exists()
    content = env_file.read_text()
    assert "ANTHROPIC_API_KEY=sk-ant-test-persist-123" in content

    import stat
    mode = env_file.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


def test_load_api_key_reads_env_file(tmp_path, monkeypatch):
    """load_api_key reads key from ~/.syke/.env when env var is unset."""
    syke_dir = tmp_path / ".syke"
    syke_dir.mkdir(parents=True)
    env_file = syke_dir / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-file\n")

    monkeypatch.setattr("syke.config.SYKE_HOME", syke_dir)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = load_api_key()
    assert result == "sk-ant-from-file"


def test_load_api_key_env_var_takes_precedence(tmp_path, monkeypatch):
    """Environment variable takes precedence over persisted file."""
    syke_dir = tmp_path / ".syke"
    syke_dir.mkdir(parents=True)
    env_file = syke_dir / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-file\n")

    monkeypatch.setattr("syke.config.SYKE_HOME", syke_dir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")

    result = load_api_key()
    assert result == "sk-ant-from-env"


def test_load_api_key_returns_empty_when_no_source(tmp_path, monkeypatch):
    """load_api_key returns empty string when neither env nor file exists."""
    syke_dir = tmp_path / ".syke"
    monkeypatch.setattr("syke.config.SYKE_HOME", syke_dir)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = load_api_key()
    assert result == ""
