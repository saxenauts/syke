"""Shared test fixtures — keeps individual test files lean."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Mock Claude SDK client (used by ask + synthesis tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ask_client():
    """Builds a mock ClaudeSDKClient with configurable responses.

    Usage:
        client, patcher = mock_ask_client(responses=[msg1, msg2])
        with patcher:
            result, cost = ask(db, user_id, "question")
    """

    def _factory(responses=None, error=None):
        async def _fake_receive():
            if error:
                raise error
            for r in responses or []:
                yield r

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.query = AsyncMock()
        client.receive_response = _fake_receive

        patcher = patch(
            "syke.distribution.ask_agent.ClaudeSDKClient", return_value=client
        )
        return client, patcher

    return _factory


# ---------------------------------------------------------------------------
# Hermes adapter environment (used by harness tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def hermes_env(tmp_path):
    """Fake Hermes home directory with config, skills, and memories."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("model:\n  default: glm-5\n")
    (hermes_home / "skills").mkdir()
    (hermes_home / "memories").mkdir()
    (hermes_home / "memories" / "MEMORY.md").write_text("Session notes here.\n")
    (hermes_home / "memories" / "USER.md").write_text("User profile here.\n")

    skill_dir = hermes_home / "skills" / "memory" / "syke"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    cat_path = hermes_home / "skills" / "memory" / "DESCRIPTION.md"

    return {
        "home": hermes_home,
        "skill_dir": skill_dir,
        "skill_path": skill_path,
        "cat_path": cat_path,
    }


@pytest.fixture
def hermes_patches(hermes_env):
    """Context manager that patches all Hermes path constants."""
    env = hermes_env

    def _apply():
        return (
            patch("syke.distribution.harness.hermes.HERMES_HOME", env["home"]),
            patch(
                "syke.distribution.harness.hermes.HERMES_SKILLS",
                env["home"] / "skills",
            ),
            patch(
                "syke.distribution.harness.hermes.SYKE_CATEGORY",
                env["home"] / "skills" / "memory",
            ),
            patch("syke.distribution.harness.hermes.SYKE_SKILL_DIR", env["skill_dir"]),
            patch(
                "syke.distribution.harness.hermes.SYKE_SKILL_PATH", env["skill_path"]
            ),
            patch(
                "syke.distribution.harness.hermes.CATEGORY_DESC_PATH", env["cat_path"]
            ),
        )

    return _apply
