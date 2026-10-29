"""Shared test fixtures — keeps individual test files lean."""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

_TEST_HOME_ROOT = Path(tempfile.mkdtemp(prefix="syke-pytest-home-")).resolve()
_TEST_HOME = _TEST_HOME_ROOT / "home"

_TEST_HOME.mkdir(parents=True, exist_ok=True)

# Set synthetic user roots before test modules import Syke code that binds paths
# from Path.home() or os.path.expanduser("~") at import time.
os.environ["HOME"] = str(_TEST_HOME)

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
def isolated_syke_logging():
    syke_logger = logging.getLogger("syke")

    def reset_handlers() -> None:
        for handler in list(syke_logger.handlers):
            syke_logger.removeHandler(handler)
            handler.close()
        syke_logger.propagate = True

    reset_handlers()
    try:
        yield
    finally:
        reset_handlers()


@pytest.fixture
def cli_runner(isolated_syke_logging):
    """Click CLI test runner with process-global logging contained to the test."""
    return CliRunner()


@pytest.fixture(autouse=True)
def isolate_runtime_paths(tmp_path, monkeypatch):
    """Keep tests from mutating the developer's real Syke workspace or Pi state."""
    import syke.config as config
    import syke.config_file as config_file
    from syke.runtime import workspace

    home_dir = tmp_path / "home"
    syke_home = home_dir / ".syke"
    control_root = syke_home / "control"
    workspace_root = syke_home / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    pi_agent_dir = tmp_path / "pi-agent"
    pi_state_audit_path = tmp_path / "pi-state-audit.log"

    home_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("SYKE_PROVIDER", raising=False)
    monkeypatch.delenv("SYKE_DB", raising=False)
    monkeypatch.setenv("SYKE_CONTROL_ROOT", str(control_root))
    monkeypatch.setenv("SYKE_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(control_root / "tokenizers"))
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent_dir))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(pi_state_audit_path))

    monkeypatch.setattr(config, "SYKE_HOME", syke_home)
    monkeypatch.setattr(
        config,
        "SKILLS_DIRS",
        [
            home_dir / ".agents" / "skills",
            home_dir / ".claude" / "skills",
            home_dir / ".gemini" / "antigravity-cli" / "skills",
            home_dir / ".hermes" / "skills",
            home_dir / ".codex" / "skills",
            home_dir / ".cursor" / "skills",
            home_dir / ".config" / "opencode" / "skills",
        ],
    )
    monkeypatch.setattr(config_file, "CONFIG_PATH", syke_home / "config.toml")
    monkeypatch.setattr(workspace, "SYKE_ROOT", syke_home)
    monkeypatch.setattr(workspace, "CONTROL_ROOT", control_root)
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace, "RUNTIME_DIR", control_root / "runtime")
    monkeypatch.setattr(workspace, "TEMP_DIR", control_root / "runtime" / "tmp")
    monkeypatch.setattr(workspace, "TOKENIZER_CACHE_DIR", control_root / "tokenizers")
    monkeypatch.setattr(
        workspace,
        "SESSIONS_DIR",
        control_root / "sessions",
    )
    monkeypatch.setattr(workspace, "RECEIPTS_DIR", control_root / "receipts")
    monkeypatch.setattr(workspace, "RECORDS_DIR", control_root / "records")
    monkeypatch.setattr(workspace, "SYKE_DB", workspace_root / "syke.db")
    monkeypatch.setattr(workspace, "MEMEX_PATH", workspace_root / "MEMEX.md")
    monkeypatch.setattr(workspace, "ARTIFACTS_DIR", workspace_root / "artifacts")
    monkeypatch.setattr(workspace, "HARNESS_DIR", workspace_root / "harness")
    monkeypatch.setattr(workspace, "SCRATCH_DIR", workspace_root / "scratch")

    yield


@pytest.fixture
def isolated_synthesis_paths(isolate_runtime_paths, monkeypatch):
    from syke.llm.backends import pi_synthesis
    from syke.runtime import workspace

    _ = isolate_runtime_paths
    monkeypatch.setattr(pi_synthesis, "WORKSPACE_ROOT", workspace.WORKSPACE_ROOT)
    monkeypatch.setattr(pi_synthesis, "SESSIONS_DIR", workspace.SESSIONS_DIR)
    monkeypatch.setattr(pi_synthesis, "SYKE_DB", workspace.SYKE_DB)
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", workspace.MEMEX_PATH)
