"""Tests for build_prompt() under the four-block prompt contract.

<psyche> + <now> + <memex> + <synthesis>
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from syke.db import SykeDB
from syke.models import Memory
from syke.runtime.psyche_md import SYNTHESIS_PATH, build_prompt

NOW = "2026-04-15 14:00 PDT (UTC-7)"


def test_prompt_without_db_contains_psyche_now_and_synthesis(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW)
    assert result.startswith("<psyche>")
    assert "</psyche>" in result
    assert "<now>" in result
    assert "</now>" in result
    assert f"As of: {NOW}" in result
    assert "<synthesis>" in result
    assert "</synthesis>" in result
    assert "<memex>" not in result


def test_prompt_contains_default_synthesis_block(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW)
    synthesis_content = SYNTHESIS_PATH.read_text(encoding="utf-8").strip()
    first_line = synthesis_content.split("\n")[0]
    assert first_line in result


def test_default_synthesis_block_is_cycle_directive(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW)
    synthesis_block = result[result.index("<synthesis>") : result.index("</synthesis>")]
    assert "scheduled Syke synthesis cycle" in synthesis_block
    assert "Do not wait for a user ask" in synthesis_block
    assert "Serve the ask" not in synthesis_block


def test_prompt_exposes_default_synthesis_path() -> None:
    assert SYNTHESIS_PATH.exists()


def test_prompt_requires_now() -> None:
    with pytest.raises(TypeError):
        build_prompt(Path("/tmp"))  # type: ignore[call-arg]


def test_prompt_with_empty_memex_includes_bootstrap_memex_block(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    assert "<psyche>" in result
    assert "<now>" in result
    assert "<memex>" in result
    assert "First run" in result


def test_prompt_with_real_memex_includes_memex_block(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "## Active threads\n- Working on sandbox hardening")

    result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    assert "<memex>" in result
    assert "sandbox hardening" in result


def test_prompt_with_memories_no_memex_still_includes_bootstrap_memex_block(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    db.insert_memory(Memory(id="m1", user_id=user_id, content="Test memory"))

    result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    assert "<psyche>" in result
    assert "<memex>" in result
    assert "Test memory" in result or "1 memory" in result or "1 memories" in result


def test_prompt_memex_error_swallowed(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    with patch(
        "syke.memory.memex.get_memex_for_injection",
        side_effect=RuntimeError("db exploded"),
    ):
        result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    assert "<psyche>" in result
    assert "<now>" in result
    assert "<synthesis>" in result


def test_prompt_without_synthesis_file(tmp_path: Path) -> None:
    fake_path = tmp_path / "nonexistent.md"
    result = build_prompt(tmp_path, synthesis_path=fake_path, now=NOW)
    assert "<psyche>" in result
    assert "<now>" in result
    assert "<synthesis>" not in result


def test_prompt_opt_out_of_synthesis(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW, include_synthesis=False)
    assert "<psyche>" in result
    assert "<now>" in result
    assert "<synthesis>" not in result


def test_prompt_opt_out_of_memex(tmp_path: Path, db: SykeDB, user_id: str) -> None:
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "irrelevant when skipped")

    result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, include_memex=False)
    assert "<psyche>" in result
    assert "<now>" in result
    assert "<memex>" not in result
    assert "irrelevant when skipped" not in result


def test_prompt_includes_adapter_block(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW)
    assert "<adapters>" in result


def test_prompt_respects_selected_sources_filter(tmp_path: Path) -> None:
    adapters_dir = tmp_path / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    (adapters_dir / "codex.md").write_text("# codex", encoding="utf-8")
    (adapters_dir / "claude-code.md").write_text("# claude", encoding="utf-8")

    result = build_prompt(
        tmp_path,
        now=NOW,
        selected_sources=("codex",),
    )

    assert "**codex**" in result
    assert "**claude-code**" not in result


def test_prompt_uses_tagged_structure(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW)
    assert "<psyche>" in result
    assert "</psyche>" in result
    assert "<now>" in result
    assert "</now>" in result
    assert "<synthesis>" in result
    assert "---" not in result


def test_prompt_now_block_appears_between_psyche_and_memex(
    tmp_path: Path, db: SykeDB, user_id: str
) -> None:
    from syke.memory.memex import update_memex

    update_memex(db, user_id, "## Active threads\n- Time-sensitive thread")

    result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    psyche_end = result.index("</psyche>")
    now_start = result.index("<now>")
    memex_start = result.index("<memex>")
    assert psyche_end < now_start < memex_start
    assert "/ 2,000 tokens" in result


def test_prompt_includes_temporal_fields_in_now_block(
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
    # Temporal fields now land in <now>, not <memex>
    now_block = result[result.index("<now>") : result.index("</now>")]
    assert "As of: 2026-04-15 14:00 PDT (UTC-7)" in now_block
    assert "Cycle #12" in now_block
    assert "Last cycle: 2026-04-15 13:45 PDT" in now_block
    # Directive is present
    assert "Ignore host `date`" in now_block
    # These fields no longer live in <memex>
    memex_block = result[result.index("<memex>") : result.index("</memex>")]
    assert "Now:" not in memex_block
    assert "Cycle: #12" not in memex_block


def test_prompt_time_directive_can_be_disabled(tmp_path: Path) -> None:
    result = build_prompt(tmp_path, now=NOW, time_directive=False)
    now_block = result[result.index("<now>") : result.index("</now>")]
    assert f"As of: {NOW}" in now_block
    assert "Ignore host `date`" not in now_block
