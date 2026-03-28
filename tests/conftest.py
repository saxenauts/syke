"""Shared test fixtures — keeps individual test files lean."""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from unittest.mock import patch

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
            patch("syke.distribution.harness.hermes.SYKE_SKILL_PATH", env["skill_path"]),
            patch("syke.distribution.harness.hermes.CATEGORY_DESC_PATH", env["cat_path"]),
        )

    return _apply
