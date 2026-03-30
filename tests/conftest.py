"""Shared test fixtures — keeps individual test files lean."""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from syke.db import SykeDB

# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite database per test."""
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
    """Keep tests from mutating the developer's real Syke workspace."""
    import syke.config as config
    from syke.runtime import workspace

    data_dir = tmp_path / "data"
    workspace_root = tmp_path / "workspace"
    original_bindings = workspace.workspace_bindings()

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    workspace.set_workspace_root(workspace_root)

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
        monkeypatch.setattr(module, "EVENTS_DB", workspace.EVENTS_DB, raising=False)
        monkeypatch.setattr(module, "SYKE_DB", workspace.SYKE_DB, raising=False)
        monkeypatch.setattr(module, "MEMEX_PATH", workspace.MEMEX_PATH, raising=False)

    try:
        yield
    finally:
        workspace.set_workspace_root(original_bindings["WORKSPACE_ROOT"])
