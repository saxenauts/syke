from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

import pytest

from syke.db import SykeDB
from syke.distribution import refresh_distribution
from syke.distribution.context_files import (
    distribute_memex,
    ensure_claude_include,
    install_skill,
)
from syke.distribution.harness import (
    get_detected_adapters,
    install_all,
)
from syke.distribution.harness.base import AdapterResult
from syke.models import Memory


class HermesEnv(TypedDict):
    home: Path
    skill_dir: Path
    skill_path: Path
    cat_path: Path


PatchFactory = Callable[[], tuple[AbstractContextManager[object], ...]]


# --- Hermes detection ---


@pytest.mark.parametrize(
    ("mode", "expected_detected"),
    [
        ("installed", True),
        ("no_hermes", False),
    ],
)
def test_detect_hermes_installation_states(
    mode: str,
    expected_detected: bool,
    hermes_env: HermesEnv,
    tmp_path: Path,
) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    hermes_home: Path
    if mode == "installed":
        hermes_home = hermes_env["home"]
    elif mode == "no_hermes":
        hermes_home = tmp_path / "missing-hermes"
    else:
        hermes_home = tmp_path / "missing-hermes"

    with patch("syke.distribution.harness.hermes.HERMES_HOME", hermes_home):
        assert HermesAdapter().detect() is expected_detected


# --- Hermes install ---


def test_install_writes_skill_and_category_files(
    hermes_env: HermesEnv,
    hermes_patches: PatchFactory,
) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    with ExitStack() as stack:
        for p in hermes_patches():
            _ = stack.enter_context(p)
        result = HermesAdapter().install()

    assert result.ok
    assert hermes_env["skill_path"].exists()
    assert hermes_env["cat_path"].exists()
    assert len(result.installed) == 2
    assert "name: syke" in hermes_env["skill_path"].read_text()
    assert "Memory and context skills" in hermes_env["cat_path"].read_text()


def test_install_preserves_native_memory_files(
    hermes_env: HermesEnv,
    hermes_patches: PatchFactory,
) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    mem_path = hermes_env["home"] / "memories" / "MEMORY.md"
    user_path = hermes_env["home"] / "memories" / "USER.md"
    mem_before = mem_path.read_text()
    user_before = user_path.read_text()

    with ExitStack() as stack:
        for p in hermes_patches():
            _ = stack.enter_context(p)
        _ = HermesAdapter().install()

    assert mem_path.read_text() == mem_before
    assert user_path.read_text() == user_before


def test_install_skips_when_hermes_not_detected(tmp_path: Path) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    with patch("syke.distribution.harness.hermes.HERMES_HOME", tmp_path / "nope"):
        result = HermesAdapter().install()

    assert not result.ok
    assert len(result.skipped) == 1
    assert "not installed" in result.skipped[0].lower()


def test_install_uses_custom_skill_content(
    hermes_env: HermesEnv,
    hermes_patches: PatchFactory,
) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    custom = "---\nname: syke\n---\nCustom content.\n"

    with ExitStack() as stack:
        for p in hermes_patches():
            _ = stack.enter_context(p)
        _ = HermesAdapter().install(skill_content=custom)

    assert hermes_env["skill_path"].read_text() == custom


# --- Hermes status ---


@pytest.mark.parametrize(
    ("mode", "expected_detected", "expected_connected"),
    [
        ("connected", True, True),
        ("not_connected", True, False),
    ],
)
def test_status_reports_detection_and_connection(
    mode: str,
    expected_detected: bool,
    expected_connected: bool,
    hermes_patches: PatchFactory,
) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    if mode == "connected":
        with ExitStack() as stack:
            for p in hermes_patches():
                _ = stack.enter_context(p)
            _ = HermesAdapter().install()
            status = HermesAdapter().status()
    elif mode == "not_connected":
        with ExitStack() as stack:
            for p in hermes_patches():
                _ = stack.enter_context(p)
            status = HermesAdapter().status()
    else:
        with ExitStack() as stack:
            for p in hermes_patches():
                _ = stack.enter_context(p)
            status = HermesAdapter().status()

    assert status.detected is expected_detected
    assert status.connected is expected_connected
    if mode == "connected":
        assert "MEMORY.md" in status.notes
        assert "USER.md" in status.notes


# --- Hermes uninstall ---


def test_uninstall_removes_skill(tmp_path: Path) -> None:
    from syke.distribution.harness.hermes import HermesAdapter

    skill_dir = tmp_path / "syke"
    skill_path = skill_dir / "SKILL.md"
    skill_dir.mkdir(parents=True)
    _ = skill_path.write_text("---\nname: syke\n---\n")

    with (
        patch("syke.distribution.harness.hermes.SYKE_SKILL_PATH", skill_path),
        patch("syke.distribution.harness.hermes.SYKE_SKILL_DIR", skill_dir),
    ):
        result = HermesAdapter().uninstall()

    assert result
    assert not skill_path.exists()
    assert not skill_dir.exists()


def test_get_detected_adapters_filters_undetected(tmp_path: Path) -> None:
    with patch("syke.distribution.harness.hermes.HERMES_HOME", tmp_path / "nope"):
        adapters = get_detected_adapters()
    names = [a.name for a in adapters]
    assert "hermes" not in names


def test_install_all_runs_for_detected_adapters(hermes_patches: PatchFactory) -> None:
    with ExitStack() as stack:
        for p in hermes_patches():
            _ = stack.enter_context(p)
        results = install_all()

    assert "hermes" in results
    assert results["hermes"].ok


# --- Formatters ---


# --- Context files ---


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

    assert out_path == tmp_path / "CLAUDE.md"
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
    assert not (tmp_path / "CLAUDE.md").exists()


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
    assert content.count(".syke/data/test_user/CLAUDE.md") == 1


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
    assert "@~/.syke/data/test_user/CLAUDE.md" in global_path.read_text()


def test_install_skill_installs_only_to_detected_platforms(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    cursor_dir = tmp_path / ".cursor"
    claude_dir.mkdir()
    cursor_dir.mkdir()

    skills_dirs = [
        claude_dir / "skills",
        cursor_dir / "skills",
        tmp_path / ".codex" / "skills",
    ]

    with patch("syke.distribution.context_files.SKILLS_DIRS", skills_dirs):
        installed_paths = install_skill()

    assert len(installed_paths) == 2
    assert (claude_dir / "skills" / "syke" / "SKILL.md").exists()
    assert (cursor_dir / "skills" / "syke" / "SKILL.md").exists()
    assert not (tmp_path / ".codex" / "skills" / "syke" / "SKILL.md").exists()


def test_refresh_distribution_orchestrates_exports(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    memex_path = tmp_path / "data" / "CLAUDE.md"
    memex_path.parent.mkdir(parents=True)
    global_path = tmp_path / ".claude" / "CLAUDE.md"
    global_path.parent.mkdir(parents=True)
    skill_path = tmp_path / ".codex" / "skills" / "syke" / "SKILL.md"
    harness_path = tmp_path / ".hermes" / "skills" / "memory" / "syke" / "SKILL.md"

    with (
        patch("syke.distribution.distribute_memex", return_value=memex_path) as distribute,
        patch("syke.distribution.CLAUDE_GLOBAL_MD", global_path),
        patch("syke.distribution.ensure_claude_include", return_value=True) as include,
        patch("syke.distribution.install_skill", return_value=[skill_path]) as install_skills,
        patch(
            "syke.distribution.install_all",
            return_value={"hermes": AdapterResult(installed=[harness_path])},
        ) as install_harness,
        patch("syke.memory.memex.get_memex_for_injection", return_value="# Memex"),
    ):
        result = refresh_distribution(db, user_id)

    distribute.assert_called_once_with(db, user_id)
    include.assert_called_once_with(user_id)
    install_skills.assert_called_once_with()
    install_harness.assert_called_once_with(memex="# Memex")
    assert result.memex_path == memex_path
    assert result.claude_include_ready is True
    assert result.skill_paths == [skill_path]
    assert result.harness_results["hermes"].ok is True
    assert result.warnings == []


def test_refresh_distribution_skips_claude_include_without_claude_dir(
    db: SykeDB,
    user_id: str,
    tmp_path: Path,
) -> None:
    memex_path = tmp_path / "data" / "CLAUDE.md"
    memex_path.parent.mkdir(parents=True)
    global_path = tmp_path / ".claude" / "CLAUDE.md"

    with (
        patch("syke.distribution.distribute_memex", return_value=memex_path),
        patch("syke.distribution.CLAUDE_GLOBAL_MD", global_path),
        patch("syke.distribution.ensure_claude_include") as include,
        patch("syke.distribution.install_skill", return_value=[]),
        patch("syke.distribution.install_all", return_value={}),
        patch("syke.memory.memex.get_memex_for_injection", return_value="# Memex"),
    ):
        result = refresh_distribution(db, user_id)

    include.assert_not_called()
    assert result.claude_include_ready is False
