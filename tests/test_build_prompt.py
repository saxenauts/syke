"""Tests for build_prompt() — the unified prompt constructor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from syke.db import SykeDB
from syke.models import Memory
from syke.runtime.psyche_md import SKILL_PATH, build_prompt


def test_prompt_without_db(tmp_path: Path) -> None:
    """No db/user_id → PSYCHE + skill, no MEMEX."""
    result = build_prompt(tmp_path)
    assert "You are Syke" in result
    assert "First run" not in result  # bootstrap message only for empty DB


def test_prompt_contains_skill(tmp_path: Path) -> None:
    """Skill prompt from pi_synthesis.md is included."""
    result = build_prompt(tmp_path)
    skill_content = SKILL_PATH.read_text(encoding="utf-8")
    # At least the first substantive line should be present
    first_line = skill_content.strip().split("\n")[0]
    assert first_line in result


def test_prompt_with_empty_memex(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    """db + user_id but no data → bootstrap guidance injected."""
    result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "You are Syke" in result
    assert "First run" in result
    assert "adapters/" in result


def test_prompt_with_real_memex(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    """db + user_id with memex content → all three sections present."""
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "## Active threads\n- Working on sandbox hardening")

    result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "You are Syke" in result
    assert "sandbox hardening" in result


def test_prompt_with_memories_no_memex(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    """Memories exist but no memex → fallback stats injected."""
    db.insert_memory(Memory(id="m1", user_id=user_id, content="Test memory"))

    result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "You are Syke" in result
    assert "1 memories" in result or "1 memory" in result


def test_prompt_memex_error_swallowed(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    """If get_memex_for_injection raises, prompt still works (PSYCHE + skill)."""
    with patch(
        "syke.memory.memex.get_memex_for_injection",
        side_effect=RuntimeError("db exploded"),
    ):
        result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "You are Syke" in result


def test_prompt_without_skill_file(tmp_path: Path) -> None:
    """If skill file is missing, prompt still works."""
    import syke.runtime.psyche_md as mod

    fake_path = tmp_path / "nonexistent.md"
    original = mod.SKILL_PATH
    mod.SKILL_PATH = fake_path
    try:
        result = build_prompt(tmp_path)
    finally:
        mod.SKILL_PATH = original
    assert "You are Syke" in result


def test_prompt_includes_adapter_block(tmp_path: Path) -> None:
    """PSYCHE section includes adapter block (even if empty)."""
    result = build_prompt(tmp_path)
    assert "## Adapters" in result


def test_prompt_structure_separators(tmp_path: Path) -> None:
    """Prompt has --- separators between sections."""
    result = build_prompt(tmp_path)
    assert "---" in result
