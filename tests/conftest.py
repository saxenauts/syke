"""Shared test fixtures — keeps individual test files lean."""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

_TEST_HOME_ROOT = Path(tempfile.mkdtemp(prefix="syke-pytest-home-")).resolve()
_TEST_HOME = _TEST_HOME_ROOT / "home"
_TEST_XDG_CONFIG = _TEST_HOME_ROOT / "xdg-config"
_TEST_XDG_DATA = _TEST_HOME_ROOT / "xdg-data"
_TEST_XDG_CACHE = _TEST_HOME_ROOT / "xdg-cache"

for _path in (_TEST_HOME, _TEST_XDG_CONFIG, _TEST_XDG_DATA, _TEST_XDG_CACHE):
    _path.mkdir(parents=True, exist_ok=True)

# Set synthetic user roots before test modules import Syke code that binds paths
# from Path.home() or os.path.expanduser("~") at import time.
os.environ["HOME"] = str(_TEST_HOME)
os.environ["XDG_CONFIG_HOME"] = str(_TEST_XDG_CONFIG)
os.environ["XDG_DATA_HOME"] = str(_TEST_XDG_DATA)
os.environ["XDG_CACHE_HOME"] = str(_TEST_XDG_CACHE)

atexit.register(lambda: shutil.rmtree(_TEST_HOME_ROOT, ignore_errors=True))

# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite database per test."""
    from syke.db import SykeDB

    with SykeDB(tmp_path / "test.db") as database:
        yield database


@pytest.fixture
def user_id():
    return "test_user"


@pytest.fixture
def cli_runner():
    """Click CLI test runner."""
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_runtime_paths(tmp_path, monkeypatch):
    """Keep tests from mutating the developer's real Syke workspace or Pi state."""
    import syke.config as config
    import syke.config_file as config_file
    from syke.runtime import workspace

    home_dir = tmp_path / "home"
    syke_home = home_dir / ".syke"
    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "workspace"
    xdg_config_home = home_dir / ".config"
    xdg_data_home = home_dir / ".local" / "share"
    xdg_cache_home = home_dir / ".cache"
    pi_agent_dir = tmp_path / "pi-agent"
    pi_state_audit_path = tmp_path / "pi-state-audit.log"

    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("SYKE_PROVIDER", raising=False)
    monkeypatch.delenv("SYKE_DB", raising=False)
    monkeypatch.delenv("SYKE_EVENTS_DB", raising=False)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache_home))
    monkeypatch.setenv("SYKE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent_dir))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(pi_state_audit_path))
    original_workspace_root = workspace.WORKSPACE_ROOT

    monkeypatch.setattr(config, "SYKE_HOME", syke_home)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CODEX_DIR", home_dir / ".codex")
    monkeypatch.setattr(config, "CODEX_GLOBAL_AGENTS", home_dir / ".codex" / "AGENTS.md")
    monkeypatch.setattr(config, "CLAUDE_GLOBAL_MD", home_dir / ".claude" / "CLAUDE.md")
    monkeypatch.setattr(
        config,
        "SKILLS_DIRS",
        [
            home_dir / ".agents" / "skills",
            home_dir / ".claude" / "skills",
            home_dir / ".gemini" / "skills",
            home_dir / ".hermes" / "skills",
            home_dir / ".codex" / "skills",
            home_dir / ".cursor" / "skills",
            home_dir / ".config" / "opencode" / "skills",
        ],
    )
    monkeypatch.setattr(config_file, "CONFIG_PATH", syke_home / "config.toml")
    workspace.set_workspace_root(workspace_root)

    loaded_module_overrides = {
        "syke.config_file": {
            "CONFIG_PATH": syke_home / "config.toml",
        },
        "syke.distribution.context_files": {
            "CURSOR_COMMANDS_DIR": home_dir / ".cursor" / "commands",
            "COPILOT_AGENTS_DIR": home_dir / ".copilot" / "agents",
            "ANTIGRAVITY_WORKFLOWS_DIR": home_dir / ".gemini" / "antigravity" / "global_workflows",
        },
        "syke.llm.pi_client": {
            "PI_LOCAL_PREFIX": syke_home / "pi",
            "PI_BIN": syke_home / "bin" / "pi",
            "PI_NODE_BIN": syke_home / "bin" / "node",
        },
        "syke.pi_state": {
            "SYKE_HOME": syke_home,
        },
        "syke.runtime.locator": {
            "SYKE_HOME": syke_home,
            "SYKE_BIN_DIR": syke_home / "bin",
        },
        "syke.version_check": {
            "SYKE_HOME": syke_home,
        },
    }
    for module_name, attrs in loaded_module_overrides.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr_name, value in attrs.items():
            monkeypatch.setattr(module, attr_name, value, raising=False)

    for module_name in (
        "syke.llm.backends.pi_synthesis",
        "syke.llm.backends.pi_ask",
        "syke.llm.simple",
    ):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        monkeypatch.setattr(module, "WORKSPACE_ROOT", workspace.WORKSPACE_ROOT, raising=False)
        monkeypatch.setattr(module, "SESSIONS_DIR", workspace.SESSIONS_DIR, raising=False)
        monkeypatch.setattr(module, "SYKE_DB", workspace.SYKE_DB, raising=False)
        monkeypatch.setattr(module, "MEMEX_PATH", workspace.MEMEX_PATH, raising=False)

    try:
        yield
    finally:
        workspace.set_workspace_root(original_workspace_root)


@pytest.fixture(autouse=True)
def reset_syke_logging():
    """Prevent logger handler state from leaking across tests."""
    syke_logger = logging.getLogger("syke")
    syke_logger.handlers.clear()
    syke_logger.propagate = True
    yield
    syke_logger.handlers.clear()
    syke_logger.propagate = True
