from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from syke.db import SykeDB
from syke.distribution import refresh_distribution
from syke.distribution.context_files import (
    distribute_memex,
    install_skill,
)
from syke.models import Memory


def test_distribute_memex_writes_file_with_preamble(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    _ = db.insert_memory(
        Memory(
            id="memex-001",
            user_id=user_id,
            content="# Memex — test_user\n\n## Identity\nTest identity.",
            source_event_ids=["__memex__"],
        )
    )

    with patch("syke.config.user_data_dir", return_value=tmp_path):
        out_path = distribute_memex(db, user_id)

    assert out_path == tmp_path / "MEMEX.md"
    assert out_path is not None
    written = out_path.read_text()
    assert "# Syke" in written
    assert "auto-generated" in written
    assert "# Memex — test_user" in written
    assert "Test identity." in written


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
    claude_dir = tmp_path / ".claude"
    gemini_dir = tmp_path / ".gemini"
    hermes_dir = tmp_path / ".hermes"
    cursor_dir = tmp_path / ".cursor"
    copilot_dir = tmp_path / ".copilot"
    opencode_config_dir = tmp_path / ".config" / "opencode"
    antigravity_workflows_dir = gemini_dir / "antigravity" / "global_workflows"
    agents_dir.mkdir()
    claude_dir.mkdir()
    gemini_dir.mkdir()
    hermes_dir.mkdir()
    cursor_dir.mkdir()
    copilot_dir.mkdir()
    opencode_config_dir.mkdir(parents=True)
    antigravity_workflows_dir.mkdir(parents=True)

    skills_dirs = [
        agents_dir / "skills",
        claude_dir / "skills",
        gemini_dir / "skills",
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
        installed_paths = install_skill("test_user")

    assert len(installed_paths) == 9
    assert (agents_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (claude_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (gemini_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (hermes_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (cursor_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (opencode_config_dir / "skills" / "syke" / "SKILL.md").exists()
    assert not (tmp_path / ".codex" / "skills" / "syke" / "SKILL.md").exists()
    assert (cursor_dir / "commands" / "syke.md").exists()
    assert (copilot_dir / "agents" / "syke.agent.md").exists()
    assert (antigravity_workflows_dir / "syke.md").exists()
    skill_text = (claude_dir / "skills" / "syke" / "SKILL.md").read_text()
    assert "~/.syke/MEMEX.md" in skill_text


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
    install_skills.assert_called_once_with(user_id)
    assert result.memex_path == memex_path
    assert result.skill_paths == [skill_path]
    assert result.warnings == []
    assert result.status_lines() == [
        ("memex", "exported", str(memex_path)),
        ("capabilities", "registered", "1 file"),
    ]


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
    assert result.status_lines() == [
        ("memex", "pending", "no memex available yet"),
        ("capabilities", "none", "no capability surfaces detected"),
    ]
