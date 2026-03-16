"""Sandbox fixtures — isolated DB and adapter factories."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from syke.db import SykeDB


SANDBOX_USER = "sandbox-user"


@pytest.fixture
def sandbox_db(tmp_path):
    """Fresh isolated DB for sandbox tests."""
    with SykeDB(tmp_path / "sandbox.db") as db:
        yield db


@pytest.fixture
def user_id():
    return SANDBOX_USER


@pytest.fixture
def sandbox_dir(tmp_path):
    """Root directory that acts as HOME for adapter discovery."""
    d = tmp_path / "sandbox"
    d.mkdir()
    (d / ".claude" / "projects").mkdir(parents=True)
    (d / ".claude" / "transcripts").mkdir(parents=True)
    (d / ".codex" / "sessions").mkdir(parents=True)
    return d


@pytest.fixture
def claude_adapter(sandbox_db, user_id, sandbox_dir):
    """ClaudeCodeAdapter pointed at sandbox_dir as HOME."""
    from syke.ingestion.claude_code import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter(sandbox_db, user_id)
    return adapter, sandbox_dir


@pytest.fixture
def codex_adapter(sandbox_db, user_id, sandbox_dir):
    """CodexAdapter pointed at sandbox_dir as HOME."""
    from syke.ingestion.codex import CodexAdapter

    adapter = CodexAdapter(sandbox_db, user_id)
    return adapter, sandbox_dir


def run_adapter(adapter, home_dir):
    """Run adapter.ingest() with HOME patched to the sandbox directory."""
    with patch.dict("os.environ", {"HOME": str(home_dir)}):
        return adapter.ingest()
