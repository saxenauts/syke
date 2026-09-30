"""System contracts for Syke's operative prompt assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from syke.db import SykeDB
from syke.memory.memex import update_memex
from syke.memory.memex_budget import warm_memex_tokenizer
from syke.runtime.prompt_context import build_prompt

NOW = "2026-04-15 14:00 PDT (UTC-7)"
CLOCK_LINE = f"- Now: {NOW}. Use this, not the system clock, for today and for relative dates."
WAKE_SURVIVAL = (
    "If the run times out, fails, or is rejected, graph and MEMEX changes go back to where "
    "they were when this run started. Files in your workspace, including OPERATING.md, and in "
    "this run's folder stay, so write a lesson down when you learn it."
)
ASK_SURVIVAL = "What you write during an ask stays; nothing is checked or rolled back."
RECORDS_BLOCK = (
    "## Records\n\nNotes other agents recorded with `syke record` that no completed wake has "
    'taken in yet.\n\nrecord record-1\npayload: "new evidence"'
)
REMOVED_PHRASES = (
    "Work owed",
    "unavailable in the current host",
    "Source-change account",
    "Evidence presented now",
    "Why this invocation exists",
    "obligation",
    "Output route",
    "host-accepted",
    "file mtimes",
    "replay",
)


def test_prompt_assembles_state_and_operation_without_writes(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    row_id = update_memex(db, user_id, "## Active threads\n- Verify self-continuation")
    warm_memex_tokenizer()
    graph_changes = db.conn.total_changes
    files_before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    result = build_prompt(
        tmp_path,
        db=db,
        user_id=user_id,
        now=NOW,
        context="ask",
        operation_id="cycle-ask",
        answer_obligation="What changed?",
        time_limit_s=600,
    )

    sections = ["# Self-observation", "# MEMEX", "# Operating notes", "# This run"]
    assert all(result.count(section) == 1 for section in sections)
    assert [result.index(section) for section in sections] == sorted(
        result.index(section) for section in sections
    )
    for value in (
        user_id,
        str(Path(db.db_path).resolve()),
        str(tmp_path.resolve()),
        row_id,
        "Verify self-continuation",
        "cycle-ask",
        "Question: What changed?",
        CLOCK_LINE,
    ):
        assert value in result
    assert db.conn.total_changes == graph_changes
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == files_before


def test_memex_header_states_the_hard_limit_once(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    update_memex(db, user_id, "## Active threads\n- Verify self-continuation")
    result = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="synthesis")
    memex = result[result.index("# MEMEX") : result.index("# Operating notes")]

    assert "/ 2,000 tokens. That is a hard limit; the host rejects a run" in memex
    assert "not to advance a timestamp, mirror telemetry, or show activity" in memex
    assert "- Verify self-continuation" in memex
    assert result.count("/ 2,000 tokens") == 1


@pytest.mark.parametrize(
    ("context", "first_run_guidance", "limit", "shown"),
    [
        ("synthesis", "", 600.0, "Time limit: 600 s."),
        ("synthesis", "Inspect the bounded source inventory.", 1500.0, "Time limit: 1,500 s."),
        ("replay", "", 600.0, "Time limit: 600 s."),
    ],
)
def test_wake_block_states_limit_survival_and_reference_time(
    tmp_path: Path,
    context: str,
    first_run_guidance: str,
    limit: float,
    shown: str,
) -> None:
    result = build_prompt(
        tmp_path,
        now=NOW,
        context=context,
        operation_id="cycle-1",
        condition="first run" if first_run_guidance else "ordinary",
        first_run_guidance=first_run_guidance,
        time_limit_s=limit,
    )

    assert "- Kind: wake" in result
    assert "- Operation ID: cycle-1" in result
    assert CLOCK_LINE in result
    assert f"- {shown} {WAKE_SURVIVAL}" in result
    assert "No one is waiting." in result
    assert "The host doesn't track what changed in your sources" in result
    assert "write down the blocker and where the evidence is" in result
    assert ASK_SURVIVAL not in result
    for phrase in REMOVED_PHRASES:
        assert phrase not in result
    if first_run_guidance:
        assert "## First run\n\nInspect the bounded source inventory." in result
    else:
        assert "## First run" not in result


def test_replay_renders_exactly_as_a_wake(tmp_path: Path) -> None:
    kwargs = dict(now=NOW, operation_id="cycle-1", time_limit_s=600.0)
    assert build_prompt(tmp_path, context="replay", **kwargs) == build_prompt(
        tmp_path, context="synthesis", **kwargs
    )


def test_ask_block_states_limit_and_that_writes_stay(tmp_path: Path) -> None:
    result = build_prompt(
        tmp_path,
        now=NOW,
        context="ask",
        operation_id="ask-1",
        answer_obligation="Where is the release checklist?",
        time_limit_s=600,
    )

    assert "- Kind: ask" in result
    assert CLOCK_LINE in result
    assert f"- Time limit: 600 s. {ASK_SURVIVAL}" in result
    assert "Question: Where is the release checklist?" in result
    assert "Someone is waiting." in result
    assert "Asked by" not in result
    assert "- Condition:" not in result
    assert WAKE_SURVIVAL not in result
    for phrase in REMOVED_PHRASES:
        assert phrase not in result


def test_time_limit_line_is_omitted_when_unknown(tmp_path: Path) -> None:
    wake = build_prompt(tmp_path, now=NOW, context="synthesis")
    ask = build_prompt(tmp_path, now=NOW, context="ask")
    assert "Time limit" not in wake
    assert f"- {WAKE_SURVIVAL}" in wake
    assert f"- {ASK_SURVIVAL}" in ask


def test_records_header_appears_once_and_only_with_records(tmp_path: Path) -> None:
    with_records = build_prompt(
        tmp_path,
        now=NOW,
        context="synthesis",
        incoming_records=RECORDS_BLOCK,
    )
    without_records = build_prompt(tmp_path, now=NOW, context="synthesis")

    assert with_records.count("## Records") == 1
    assert with_records.count("recorded with `syke record`") == 1
    assert 'payload: "new evidence"' in with_records
    assert with_records.index("No one is waiting.") < with_records.index("## Records")
    assert "## Records" not in without_records
    assert "syke record" not in without_records


def test_optional_guidance_is_appended(tmp_path: Path) -> None:
    guidance = tmp_path / "condition.md"
    guidance.write_text("condition-specific guidance", encoding="utf-8")

    assert "condition-specific guidance" in build_prompt(
        tmp_path,
        now=NOW,
        context="synthesis",
        synthesis_path=guidance,
    )


def test_prompt_dependency_failures_degrade_without_disclosing_exceptions(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    with patch(
        "syke.runtime.self_view.build_self_view",
        side_effect=RuntimeError("private history failure"),
    ):
        self_view_failure = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    with patch(
        "syke.memory.memex.get_memex_for_injection",
        side_effect=RuntimeError("private database failure"),
    ):
        memex_failure = build_prompt(tmp_path, db=db, user_id=user_id, now=NOW)

    assert "unknown rather than healthy" in self_view_failure
    assert "private history failure" not in self_view_failure
    assert "No current MEMEX is available" in memex_failure
    assert "private database failure" not in memex_failure
    assert "# This run" in self_view_failure
    assert "# This run" in memex_failure


def test_synthesis_prompt_fails_closed_when_memex_cannot_be_read(
    tmp_path: Path,
    db: SykeDB,
    user_id: str,
) -> None:
    with (
        patch(
            "syke.memory.memex.get_memex_for_injection",
            side_effect=RuntimeError("private database failure"),
        ),
        pytest.raises(RuntimeError, match="private database failure"),
    ):
        build_prompt(tmp_path, db=db, user_id=user_id, now=NOW, context="synthesis")
