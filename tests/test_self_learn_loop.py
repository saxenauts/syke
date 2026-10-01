"""Regression tests for Syke's operating notes and private self-learn skill."""

from __future__ import annotations

from pathlib import Path

from syke.db import SykeDB
from syke.db_safety import capture_baseline, validate_state_after_cycle
from syke.memory.learned import (
    OPERATING_NOTES_TOKEN_LIMIT,
    measure_learned_projection,
    seed_operating_notes,
)
from syke.memory.memex import update_memex
from syke.memory.memex_budget import count_memory_tokens, warm_memex_tokenizer
from syke.runtime import pi_settings
from syke.runtime.prompt_context import build_prompt

NOW = "2026-08-14 21:00 PDT (UTC-7)"


def _set_learned_row(db: SykeDB, user_id: str, content: str) -> None:
    db.conn.execute(
        """INSERT INTO memories (id, user_id, content, created_at, updated_at)
           VALUES ('syke-learned', ?, ?, '2026-01-01T00:00:00+00:00', NULL)
           ON CONFLICT(id) DO UPDATE SET content = excluded.content""",
        (user_id, content),
    )
    db.conn.commit()


def test_pi_runtime_installs_private_self_learn_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pi_agent_dir = tmp_path / "pi-agent"
    workspace = tmp_path / "workspace"
    sessions = tmp_path / "control" / "sessions"
    workspace.mkdir()
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(pi_agent_dir))

    env = pi_settings.configure_pi_workspace(workspace, session_dir=sessions)

    installed = pi_agent_dir / "skills" / "self-learn" / "SKILL.md"
    packaged = Path(pi_settings.__file__).parent / "skills" / "self-learn" / "SKILL.md"
    installed_content = installed.read_text(encoding="utf-8")
    assert env["PI_CODING_AGENT_DIR"] == str(pi_agent_dir.resolve())
    assert installed_content == packaged.read_text(encoding="utf-8")
    assert "OPERATING.md" in installed_content.split("---")[1]


def test_fresh_operations_show_the_operating_notes_file(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    notes = tmp_path / "OPERATING.md"
    notes.write_text("Reuse workspace/update_memex.py for MEMEX edits.\n", encoding="utf-8")

    ask = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="ask")
    synthesis = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="synthesis")
    for prompt in (ask, synthesis):
        assert prompt.count("# Operating notes") == 1
        assert "Reuse workspace/update_memex.py for MEMEX edits." in prompt
        assert str(notes.resolve()) in prompt
        assert prompt.index("# MEMEX") < prompt.index("# Operating notes")
        assert prompt.index("# Operating notes") < prompt.index("# This run")

    notes.write_text("Preserve useful evidence routes.\n", encoding="utf-8")
    next_ask = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="ask")
    assert "Preserve useful evidence routes." in next_ask
    assert "update_memex.py" not in next_ask


def test_missing_or_empty_notes_show_where_they_live(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    _set_learned_row(db, user_id, "old learned text that is no longer projected")
    path = str((tmp_path / "OPERATING.md").resolve())

    missing = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    (tmp_path / "OPERATING.md").write_text("  \n", encoding="utf-8")
    empty = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    for prompt in (missing, empty):
        assert "# Operating notes" in prompt
        assert f"There are no operating notes yet. They live at `{path}`" in prompt
        assert "old learned text" not in prompt
        assert "# Learned" not in prompt


def test_oversized_notes_show_the_start_and_point_to_the_file(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    lines = [f"line {index} of a long procedure" for index in range(800)]
    content = "\n".join(lines) + "\n"
    (tmp_path / "OPERATING.md").write_text(content, encoding="utf-8")
    total = count_memory_tokens(content.strip())
    assert total > OPERATING_NOTES_TOKEN_LIMIT

    prompt = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    block = prompt[prompt.index("# Operating notes") : prompt.index("# This run")]
    assert "line 0 of a long procedure" in block
    assert "line 799 of a long procedure" not in block
    assert f"The full file is `{(tmp_path / 'OPERATING.md').resolve()}`." in block
    shown = block.split(":\n\n", 1)[1].split("\n\n(About")[0]
    assert count_memory_tokens(shown) <= OPERATING_NOTES_TOKEN_LIMIT
    assert f"About {total - count_memory_tokens(shown):,} more tokens" in block


def test_seed_copies_the_learned_row_once(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    workspace = tmp_path / "workspace"
    control = tmp_path / "control"
    workspace.mkdir()
    _set_learned_row(db, user_id, "# Learned\n\n- Keep the count line honest.\n- Bound scans.")

    seeded = seed_operating_notes(db, user_id, workspace, control)

    notes = workspace / "OPERATING.md"
    assert seeded == notes.resolve()
    assert notes.read_text(encoding="utf-8") == "- Keep the count line honest.\n- Bound scans.\n"
    row = db.conn.execute("SELECT content FROM memories WHERE id = 'syke-learned'").fetchone()
    assert row["content"].startswith("# Learned")

    notes.unlink()
    assert seed_operating_notes(db, user_id, workspace, control) is None
    assert not notes.exists()


def test_seed_leaves_existing_notes_alone(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    (tmp_path / "OPERATING.md").write_text("Syke's own text\n", encoding="utf-8")
    _set_learned_row(db, user_id, "row text")

    assert seed_operating_notes(db, user_id, tmp_path, tmp_path / "control") is None
    assert (tmp_path / "OPERATING.md").read_text(encoding="utf-8") == "Syke's own text\n"


def test_prompt_with_notes_writes_nothing(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    _set_learned_row(db, user_id, "row text")
    (tmp_path / "OPERATING.md").write_text("notes\n", encoding="utf-8")
    warm_memex_tokenizer()
    graph_changes = db.conn.total_changes
    files_before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)
    (tmp_path / "OPERATING.md").unlink()
    files_before.remove(Path("OPERATING.md"))
    build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    assert db.conn.total_changes == graph_changes
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == files_before


def test_compatibility_measure_uses_the_notes_bound() -> None:
    measurement = measure_learned_projection("x " * 1_500)
    assert measurement["limit"] == OPERATING_NOTES_TOKEN_LIMIT
    assert measurement["over_budget"] is False
    assert measure_learned_projection("x " * 7_000)["over_budget"] is True


def test_semantic_gate_accepts_a_large_learned_row(
    db: SykeDB,
    user_id: str,
) -> None:
    db.bind_identity(user_id)
    update_memex(db, user_id, "canonical memex")
    baseline = capture_baseline(db, user_id)
    _set_learned_row(db, user_id, " x" * 1_500)

    verdict = validate_state_after_cycle(db, user_id, baseline)

    assert verdict["valid"] is True
    assert "learned_over_budget" not in verdict["stats"]
