from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from syke.db import SykeDB
from syke.distribution import refresh_distribution
from syke.distribution.context_files import (
    distribute_memex,
    ensure_claude_include,
    ensure_codex_memex_reference,
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
                return_value="[No data yet.]",
            ):
                out_path = distribute_memex(db, user_id)

    assert out_path is None
    assert not (tmp_path / "MEMEX.md").exists()


def test_ensure_claude_include_appends_once_and_is_idempotent(tmp_path: Path) -> None:
    global_path = tmp_path / ".claude" / "CLAUDE.md"
    global_path.parent.mkdir(parents=True)
    _ = global_path.write_text("# Existing content\n\nSome rules.")

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", global_path):
        first = ensure_claude_include("test_user")
        second = ensure_claude_include("test_user")

    content = global_path.read_text()
    assert first and second
    assert "# Existing content" in content
    assert "Some rules." in content
    assert content.count(".syke/data/test_user/MEMEX.md") == 1


def test_ensure_claude_include_returns_false_on_permission_error(
    tmp_path: Path,
) -> None:
    global_path = tmp_path / "readonly" / "CLAUDE.md"

    with (
        patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", global_path),
        patch.object(Path, "write_text", side_effect=OSError("permission denied")),
    ):
        result = ensure_claude_include("test_user")

    assert not result


def test_distribute_memex_then_include_works_end_to_end(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    _ = db.insert_memory(
        Memory(
            id="memex-e2e",
            user_id=user_id,
            content="# Memex — test_user\n\nEnd to end test.",
            source_event_ids=["__memex__"],
        )
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    global_path = tmp_path / ".claude" / "CLAUDE.md"

    with patch("syke.config.user_data_dir", return_value=data_dir):
        out_path = distribute_memex(db, user_id)

    with patch("syke.distribution.context_files.CLAUDE_GLOBAL_MD", global_path):
        include_result = ensure_claude_include(user_id)

    assert out_path is not None
    assert "# Memex — test_user" in out_path.read_text()
    assert "End to end test." in out_path.read_text()
    assert include_result
    assert "@~/.syke/data/test_user/MEMEX.md" in global_path.read_text()


def test_ensure_codex_memex_reference_appends_once_and_is_idempotent(tmp_path: Path) -> None:
    agents_path = tmp_path / ".codex" / "AGENTS.md"
    agents_path.parent.mkdir(parents=True)
    _ = agents_path.write_text("# Existing instructions\n")

    with patch("syke.distribution.context_files.CODEX_GLOBAL_AGENTS", agents_path):
        first = ensure_codex_memex_reference("test_user")
        second = ensure_codex_memex_reference("test_user")

    content = agents_path.read_text()
    assert first and second
    assert "# Existing instructions" in content
    assert content.count("syke:memex:start") == 1
    assert "~/.syke/data/test_user/MEMEX.md" in content


def test_ensure_codex_memex_reference_updates_existing_block(tmp_path: Path) -> None:
    agents_path = tmp_path / ".codex" / "AGENTS.md"
    agents_path.parent.mkdir(parents=True)
    _ = agents_path.write_text(
        "# Existing\n\n<!-- syke:memex:start -->\nold block\n<!-- syke:memex:end -->\n"
    )

    with patch("syke.distribution.context_files.CODEX_GLOBAL_AGENTS", agents_path):
        result = ensure_codex_memex_reference("test_user")

    assert result
    content = agents_path.read_text()
    assert "old block" not in content
    assert content.count("syke:memex:start") == 1
    assert "~/.syke/data/test_user/MEMEX.md" in content


def test_install_skill_installs_only_to_detected_platforms(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    cursor_dir = tmp_path / ".cursor"
    opencode_config_dir = tmp_path / ".config" / "opencode"
    claude_dir.mkdir()
    cursor_dir.mkdir()
    opencode_config_dir.mkdir(parents=True)

    skills_dirs = [
        claude_dir / "skills",
        cursor_dir / "skills",
        opencode_config_dir / "skills",
        tmp_path / ".codex" / "skills",
    ]

    with patch("syke.distribution.context_files.SKILLS_DIRS", skills_dirs):
        installed_paths = install_skill()

    assert len(installed_paths) == 3
    assert (claude_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (cursor_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (opencode_config_dir / "skills" / "syke" / "SKILL.md").exists()
    assert not (tmp_path / ".codex" / "skills" / "syke" / "SKILL.md").exists()


def test_refresh_distribution_orchestrates_exports(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    memex_path = tmp_path / "data" / "MEMEX.md"
    memex_path.parent.mkdir(parents=True)
    global_path = tmp_path / ".claude" / "CLAUDE.md"
    global_path.parent.mkdir(parents=True)
    codex_agents = tmp_path / ".codex" / "AGENTS.md"
    codex_agents.parent.mkdir(parents=True)
    skill_path = tmp_path / ".codex" / "skills" / "syke" / "SKILL.md"

    with (
        patch("syke.distribution.distribute_memex", return_value=memex_path) as distribute,
        patch("syke.distribution.CLAUDE_GLOBAL_MD", global_path),
        patch("syke.distribution.CODEX_GLOBAL_AGENTS", codex_agents),
        patch("syke.distribution.ensure_claude_include", return_value=True) as include,
        patch("syke.distribution.ensure_codex_memex_reference", return_value=True) as codex,
        patch("syke.distribution.install_skill", return_value=[skill_path]) as install_skills,
    ):
        result = refresh_distribution(db, user_id)

    distribute.assert_called_once_with(db, user_id)
    include.assert_called_once_with(user_id)
    codex.assert_called_once_with(user_id)
    install_skills.assert_called_once_with()
    assert result.memex_path == memex_path
    assert result.claude_include_ready is True
    assert result.codex_memex_ready is True
    assert result.skill_paths == [skill_path]
    assert result.warnings == []


def test_refresh_distribution_skips_claude_include_without_claude_dir(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    memex_path = tmp_path / "data" / "MEMEX.md"
    memex_path.parent.mkdir(parents=True)
    global_path = tmp_path / ".claude" / "CLAUDE.md"
    codex_agents = tmp_path / ".codex" / "AGENTS.md"

    with (
        patch("syke.distribution.distribute_memex", return_value=memex_path),
        patch("syke.distribution.CLAUDE_GLOBAL_MD", global_path),
        patch("syke.distribution.CODEX_GLOBAL_AGENTS", codex_agents),
        patch("syke.distribution.ensure_claude_include") as include,
        patch("syke.distribution.ensure_codex_memex_reference") as codex,
        patch("syke.distribution.install_skill", return_value=[]),
    ):
        result = refresh_distribution(db, user_id)

    include.assert_not_called()
    codex.assert_not_called()
    assert result.claude_include_ready is False
    assert result.codex_memex_ready is False
