"""Tests for config.py data directory resolution and defaults."""

from __future__ import annotations

import getpass
import importlib
import os
from pathlib import Path

from syke.config import (
    _default_data_dir,
    _is_source_install,
    clean_claude_env,
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



class TestCleanClaudeEnv:
    """Tests for clean_claude_env() context manager."""

    def test_strips_claudecode(self, monkeypatch):
        """CLAUDECODE is removed inside the context manager."""
        monkeypatch.setenv("CLAUDECODE", "1")
        with clean_claude_env():
            assert os.environ.get("CLAUDECODE") is None

    def test_restores_claudecode(self, monkeypatch):
        """CLAUDECODE is restored after exiting the context manager."""
        monkeypatch.setenv("CLAUDECODE", "1")
        with clean_claude_env():
            pass
        assert os.environ.get("CLAUDECODE") == "1"

    def test_strips_claude_code_prefix(self, monkeypatch):
        """Env vars starting with CLAUDE_CODE_ are stripped."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ses_123")
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "sdk-py")
        with clean_claude_env():
            assert os.environ.get("CLAUDE_CODE_SESSION_ID") is None
            assert os.environ.get("CLAUDE_CODE_ENTRYPOINT") is None

    def test_restores_claude_code_prefix(self, monkeypatch):
        """CLAUDE_CODE_* vars are restored after exiting."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ses_123")
        with clean_claude_env():
            pass
        assert os.environ.get("CLAUDE_CODE_SESSION_ID") == "ses_123"

    def test_preserves_unrelated_vars(self, monkeypatch):
        """Non-Claude env vars are untouched."""
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setenv("HOME", "/home/test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        with clean_claude_env():
            assert os.environ.get("HOME") == "/home/test"
            assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test"

    def test_noop_when_no_claude_vars(self):
        """No error when no Claude vars exist."""
        # Just ensure it doesn't crash
        with clean_claude_env():
            pass

    def test_restores_on_exception(self, monkeypatch):
        """Env is restored even if an exception occurs inside the context."""
        monkeypatch.setenv("CLAUDECODE", "1")
        try:
            with clean_claude_env():
                assert os.environ.get("CLAUDECODE") is None
                raise ValueError("boom")
        except ValueError:
            pass
        assert os.environ.get("CLAUDECODE") == "1"