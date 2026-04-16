"""Tests for build_prompt() under the three-block prompt contract."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from syke.db import SykeDB
from syke.models import Memory
from syke.runtime.psyche_md import SYNTHESIS_PATH, build_prompt


def test_prompt_without_db_contains_psyche_and_synthesis(tmp_path: Path) -> None:
    result = build_prompt(tmp_path)
    assert result.startswith("<psyche>")
    assert "</psyche>" in result
    assert "<synthesis>" in result
    assert "</synthesis>" in result
    assert "<memex>" not in result


def test_prompt_contains_default_synthesis_block(tmp_path: Path) -> None:
    result = build_prompt(tmp_path)
    synthesis_content = SYNTHESIS_PATH.read_text(encoding="utf-8").strip()
    first_line = synthesis_content.split("\n")[0]
    assert first_line in result


def test_prompt_exposes_default_synthesis_path() -> None:
    assert SYNTHESIS_PATH.exists()


def test_prompt_with_empty_memex_includes_bootstrap_memex_block(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "<psyche>" in result
    assert "<memex>" in result
    assert "First run" in result


def test_prompt_with_real_memex_includes_memex_block(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "## Active threads\n- Working on sandbox hardening")

    result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "<memex>" in result
    assert "sandbox hardening" in result


def test_prompt_with_memories_no_memex_still_includes_bootstrap_memex_block(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    db.insert_memory(Memory(id="m1", user_id=user_id, content="Test memory"))

    result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "<psyche>" in result
    assert "<memex>" in result
    assert "Test memory" in result or "1 memory" in result or "1 memories" in result


def test_prompt_memex_error_swallowed(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    with patch(
        "syke.memory.memex.get_memex_for_injection",
        side_effect=RuntimeError("db exploded"),
    ):
        result = build_prompt(tmp_path, db=db, user_id=user_id)
    assert "<psyche>" in result
    assert "<synthesis>" in result


def test_prompt_without_synthesis_file(tmp_path: Path) -> None:
    fake_path = tmp_path / "nonexistent.md"
    result = build_prompt(tmp_path, synthesis_path=fake_path)
    assert "<psyche>" in result
    assert "<synthesis>" not in result


def test_prompt_includes_adapter_block(tmp_path: Path) -> None:
    result = build_prompt(tmp_path)
    assert "<adapters>" in result


def test_prompt_uses_tagged_structure(tmp_path: Path) -> None:
    result = build_prompt(tmp_path)
    assert "<psyche>" in result
    assert "</psyche>" in result
    assert "<synthesis>" in result
    assert "---" not in result


def test_prompt_includes_temporal_fields_in_memex_header(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "## Active threads\n- Time-sensitive thread")

    result = build_prompt(
        tmp_path,
        db=db,
        user_id=user_id,
        now="2026-04-15 14:00 PDT (UTC-7)",
        last_synthesis="2026-04-15 13:45 PDT",
        cycle=12,
    )
    assert "Cycle: #12" in result
    assert "Now: 2026-04-15 14:00 PDT (UTC-7)" in result
    assert "Last cycle: 2026-04-15 13:45 PDT" in result
