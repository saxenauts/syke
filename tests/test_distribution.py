from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import syke
from syke.config import PROJECT_ROOT
from syke.db import SykeDB
from syke.distribution import refresh_distribution
from syke.distribution.context_files import (
    _get_skill_content,
    capability_target_paths,
    distribute_memex,
    install_skill,
)
from syke.memory.memex import update_memex


def test_distribute_memex_does_not_overwrite_workspace_file(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    _ = update_memex(db, user_id, "# Memex - test_user\n\n## Identity\nTest identity.")
    memex_path = tmp_path / "MEMEX.md"
    existing_content = "# MEMEX\n\nAgent-authored content\n"
    memex_path.write_text(existing_content, encoding="utf-8")

    with patch("syke.runtime.workspace.MEMEX_PATH", memex_path):
        out_path = distribute_memex(db, user_id)

    assert out_path == memex_path
    assert memex_path.read_text(encoding="utf-8") == existing_content


@pytest.mark.parametrize(
    "mode",
    ["empty", "placeholder"],
)
def test_distribute_memex_returns_none_for_empty_or_placeholder_content(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
    mode: str,
) -> None:
    with patch("syke.config.user_data_dir", return_value=tmp_path):
        if mode == "empty":
            out_path = distribute_memex(db, user_id)
        else:
            with patch(
                "syke.memory.memex.get_memex_for_injection",
                return_value="[First run — no memories yet.]",
            ):
                out_path = distribute_memex(db, user_id)

    assert out_path is None
    assert not (tmp_path / "MEMEX.md").exists()


def test_install_skill_installs_only_to_detected_platforms(tmp_path: Path) -> None:
    agents_dir = tmp_path / ".agents"
    pi_agent_dir = tmp_path / ".pi" / "agent"
    claude_dir = tmp_path / ".claude"
    gemini_dir = tmp_path / ".gemini"
    hermes_dir = tmp_path / ".hermes"
    cursor_dir = tmp_path / ".cursor"
    copilot_dir = tmp_path / ".copilot"
    opencode_config_dir = tmp_path / ".config" / "opencode"
    antigravity_workflows_dir = gemini_dir / "antigravity" / "global_workflows"
    antigravity_cli_dir = gemini_dir / "antigravity-cli"
    agents_dir.mkdir()
    pi_agent_dir.mkdir(parents=True)
    claude_dir.mkdir()
    gemini_dir.mkdir()
    hermes_dir.mkdir()
    cursor_dir.mkdir()
    copilot_dir.mkdir()
    opencode_config_dir.mkdir(parents=True)
    antigravity_workflows_dir.mkdir(parents=True)
    antigravity_cli_dir.mkdir()

    skills_dirs = [
        agents_dir / "skills",
        pi_agent_dir / "skills",
        claude_dir / "skills",
        antigravity_cli_dir / "skills",
        hermes_dir / "skills",
        tmp_path / ".codex" / "skills",
        cursor_dir / "skills",
        opencode_config_dir / "skills",
    ]

    with (
        patch("syke.distribution.context_files.SKILLS_DIRS", skills_dirs),
        patch("syke.distribution.context_files.CURSOR_COMMANDS_DIR", cursor_dir / "commands"),
        patch("syke.distribution.context_files.COPILOT_AGENTS_DIR", copilot_dir / "agents"),
        patch(
            "syke.distribution.context_files.ANTIGRAVITY_WORKFLOWS_DIR",
            antigravity_workflows_dir,
        ),
    ):
        declared_paths = capability_target_paths()
        installed_paths = install_skill()
        mtimes = {path: path.stat().st_mtime_ns for path in installed_paths}
        assert install_skill() == []
        assert {path: path.stat().st_mtime_ns for path in installed_paths} == mtimes

    assert (
        set(installed_paths)
        == set(declared_paths)
        == {
            agents_dir / "skills" / "syke" / "SKILL.md",
            pi_agent_dir / "skills" / "syke" / "SKILL.md",
            claude_dir / "skills" / "syke" / "SKILL.md",
            antigravity_cli_dir / "skills" / "syke" / "SKILL.md",
            hermes_dir / "skills" / "syke" / "SKILL.md",
            cursor_dir / "skills" / "syke" / "SKILL.md",
            opencode_config_dir / "skills" / "syke" / "SKILL.md",
            cursor_dir / "commands" / "syke.md",
            copilot_dir / "agents" / "syke.agent.md",
            antigravity_workflows_dir / "syke.md",
        }
    )
    assert all(path.exists() for path in installed_paths)
    assert not (tmp_path / ".codex" / "skills" / "syke" / "SKILL.md").exists()
    skill_text = (claude_dir / "skills" / "syke" / "SKILL.md").read_text()
    assert f"version: {syke.__version__}" in skill_text


def test_packaged_skill_matches_repo_skill_contract() -> None:
    repo_skill = (PROJECT_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert _get_skill_content() == repo_skill
    assert f"version: {syke.__version__}" in repo_skill
    assert "interim output, not the answer" in repo_skill
    assert "retain that exact ID and poll or resume it" in repo_skill
    assert "still requires waiting for process completion" in repo_skill


def test_refresh_distribution_orchestrates_exports(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    memex_path = tmp_path / "data" / "MEMEX.md"
    memex_path.parent.mkdir(parents=True)
    skill_path = tmp_path / ".codex" / "skills" / "syke" / "SKILL.md"

    with (
        patch("syke.distribution.distribute_memex", return_value=memex_path) as distribute,
        patch("syke.distribution.install_skill", return_value=[skill_path]) as install_skills,
    ):
        result = refresh_distribution(db, user_id)

    distribute.assert_called_once_with(db, user_id)
    install_skills.assert_called_once_with()
    assert result.memex_path == memex_path
    assert result.skill_paths == [skill_path]
    assert result.warnings == []


def test_refresh_distribution_installs_skill_even_without_memex(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    with (
        patch("syke.distribution.distribute_memex", return_value=None),
        patch("syke.distribution.install_skill", return_value=[]),
    ):
        result = refresh_distribution(db, user_id)

    assert result.memex_path is None
    assert result.skill_paths == []
    assert result.warnings == []
