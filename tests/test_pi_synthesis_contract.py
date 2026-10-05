from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import syke.runtime as runtime_module
from syke.config import FIRST_RUN_SYNC_TIMEOUT, SYNC_TIMEOUT
from syke.control import (
    admit_record,
    list_receipts,
    pending_records,
)
from syke.db import SykeDB
from syke.llm import pi_client
from syke.llm.backends import pi_synthesis
from syke.memory.memex import update_memex
from syke.memory.memex_budget import strip_memex_header
from syke.runtime.prompt_context import format_now_for_prompt

pytestmark = pytest.mark.usefixtures("isolated_synthesis_paths")


def _control_dir() -> Path:
    return pi_synthesis.SESSIONS_DIR.parent


def _latest_receipt() -> dict:
    receipts = list_receipts(_control_dir(), limit=1)
    assert receipts
    return receipts[0]


def _insert_memory(db: SykeDB, memory_id: str, user_id: str, content: str) -> None:
    db.conn.execute(
        """INSERT INTO memories
           (id, user_id, content, created_at, updated_at)
           VALUES (?, ?, ?, '2026-01-01T00:00:00+00:00', NULL)""",
        (memory_id, user_id, content),
    )
    db.conn.commit()


def _corrupt_search_index(db: SykeDB) -> None:
    row = db.conn.execute("SELECT id FROM memories_fts_data ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    db.conn.execute(
        "UPDATE memories_fts_data SET block = zeroblob(4) WHERE id = ?",
        (row["id"],),
    )
    db.conn.commit()


def _install_success_runtime(monkeypatch, prompt_fn) -> None:
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )
    runtime = SimpleNamespace(prompt=prompt_fn)
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)


def _pi_success_result(
    output: str = "done",
    *,
    session_name: str | None = None,
    session_id: str | None = None,
) -> SimpleNamespace:
    resolved_session_id = session_id or ("native-test-session" if session_name else None)
    return SimpleNamespace(
        ok=True,
        output=output,
        duration_ms=5,
        cost_usd=0.0,
        input_tokens=10,
        output_tokens=4,
        cache_read_tokens=0,
        cache_write_tokens=0,
        provider="kimi-coding",
        response_model="k2p5",
        response_id="resp_success",
        stop_reason="stop",
        tool_calls=[],
        events=[],
        num_turns=1,
        thinking=[],
        session_id=resolved_session_id,
        session_file=(
            f"/protected/{resolved_session_id}.jsonl" if resolved_session_id is not None else None
        ),
        session_name=session_name,
    )


def test_failed_graph_restore_still_restores_the_accepted_memex_projection(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db", user_id=user_id)
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    update_memex(db, user_id, "accepted memex")
    memex_path.write_text("accepted projection", encoding="utf-8")

    def _prompt(_prompt_text: str, **_kwargs):
        memex_path.write_text("rejected projection", encoding="utf-8")
        raise RuntimeError("runtime stopped")

    _install_success_runtime(monkeypatch, _prompt)
    monkeypatch.setattr(
        pi_synthesis,
        "restore_recovery_point",
        lambda _point, **_kwargs: (_ for _ in ()).throw(RuntimeError("restore unavailable")),
    )

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )

        assert result["status"] == "failed"
        assert "recovery" not in result
        assert "restore unavailable" in str(result["recovery_error"])
        restored_projection = memex_path.read_text(encoding="utf-8")
        assert strip_memex_header(restored_projection).strip() == "accepted memex"
    finally:
        db.close()


def test_synthesis_observes_snapshot_and_accepts_only_that_record(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db", user_id=user_id)
    update_memex(db, user_id, "canonical memex")
    payload = "line one\n" + ("x" * 5_000)
    record_id = admit_record(_control_dir(), payload)
    block, included = pi_synthesis._build_incoming_records_block(
        pending_records(_control_dir()),
        record_dir=_control_dir() / "records",
    )
    assert len(block) <= pi_synthesis.INCOMING_RECORD_CONTEXT_CHAR_LIMIT
    assert [record["id"] for record in included] == [record_id]
    assert 'payload: "line one\\n' in block
    assert "[preview; open the protected record file for the full payload]" in block
    assert str(_control_dir() / "records" / "<record_id>.json") in block
    prompts: list[str] = []
    late_record_id: str | None = None

    def _prompt(prompt: str, **_kwargs):
        nonlocal late_record_id
        prompts.append(prompt)
        if len(prompts) == 1:
            raise RuntimeError("runtime stopped")
        late_record_id = admit_record(_control_dir(), "arrived during synthesis")
        return _pi_success_result()

    _install_success_runtime(monkeypatch, _prompt)

    try:
        failed = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )
        assert failed["status"] == "failed"
        assert failed["record_ids_in_context"] == [record_id]
        assert _latest_receipt()["acknowledged_record_ids"] == []
        assert [record["id"] for record in pending_records(_control_dir())] == [record_id]

        accepted = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="base synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )

        assert accepted["status"] == "completed"
        assert accepted["record_ids_in_context"] == [record_id]
        assert len(prompts) == 2
        assert all('payload: "line one\\n' in prompt for prompt in prompts)
        assert all(prompt.count("## Records") == 1 for prompt in prompts)
        assert all("admitted since the last accepted synthesis" not in p for p in prompts)
        assert all(payload not in prompt for prompt in prompts)
        assert all("arrived during synthesis" not in prompt for prompt in prompts)
        assert _latest_receipt()["acknowledged_record_ids"] == [record_id]
        assert late_record_id is not None
        assert [record["id"] for record in pending_records(_control_dir())] == [late_record_id]
    finally:
        db.close()


@pytest.mark.parametrize(
    ("scenario", "expected_source", "expected_updated", "expected_content"),
    [
        ("db_changed", "db", True, "canonical db memex"),
        ("artifact_changed", "artifact", True, "artifact memex"),
        ("headered_db", "db", False, "canonical body"),
        ("unchanged_db", "db", False, "canonical memex"),
        ("missing_db", "previous", False, "canonical memex"),
    ],
)
def test_sync_memex_authority_matrix(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
    scenario: str,
    expected_source: str,
    expected_updated: bool,
    expected_content: str,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    previous_content = "canonical memex"
    previous_artifact_content: str | None = None
    old_id = update_memex(db, user_id, previous_content)

    if scenario == "db_changed":
        previous_content = "prior memex"
        previous_artifact_content = "stale artifact memex"
        update_memex(db, user_id, previous_content)
        update_memex(db, user_id, "canonical db memex")
        memex_path.write_text(previous_artifact_content, encoding="utf-8")
    elif scenario == "artifact_changed":
        previous_content = "prior memex"
        update_memex(db, user_id, previous_content)
        memex_path.write_text(
            "# MEMEX [10 / 2,000 tokens · 1%]\n\nartifact memex\n",
            encoding="utf-8",
        )
    elif scenario == "headered_db":
        headered = "# MEMEX [10 / 2,000 tokens · 1%]\n\ncanonical body"
        previous_content = headered
        update_memex(db, user_id, "canonical body")
        db.conn.execute(
            "UPDATE current_memex SET content = ? WHERE singleton = 1 AND user_id = ?",
            (headered, user_id),
        )
        db.conn.commit()
    elif scenario == "unchanged_db":
        previous_artifact_content = "stale artifact memex"
        memex_path.write_text(previous_artifact_content, encoding="utf-8")
    elif scenario == "missing_db":
        previous_artifact_content = previous_content
        memex_path.write_text(previous_content, encoding="utf-8")
        current = db.get_memex(user_id)
        assert current is not None
        db.conn.execute("DROP TRIGGER protect_current_memex_delete")
        db.conn.execute("DELETE FROM current_memex WHERE id = ?", (current["id"],))
        db.conn.commit()

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content=previous_content,
        previous_artifact_content=previous_artifact_content,
    )

    assert result == {
        "ok": True,
        "updated": expected_updated,
        "source": expected_source,
        "artifact_written": True,
    }
    active = db.get_memex(user_id)
    assert active is not None
    if scenario != "missing_db":
        assert active["id"] == old_id
    assert active["content"] == expected_content
    written = memex_path.read_text(encoding="utf-8")
    assert written.startswith("# MEMEX [")
    assert "/ 2,000 tokens" in written
    assert strip_memex_header(written).strip() == expected_content


def test_pi_synthesize_treats_header_only_memex_normalization_as_noop(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    update_memex(db, user_id, "canonical body")
    headered = "# MEMEX [10 / 2,000 tokens · 1%]\n\ncanonical body"
    db.conn.execute(
        "UPDATE current_memex SET content = ? WHERE singleton = 1 AND user_id = ?",
        (headered, user_id),
    )
    db.conn.commit()

    def _prompt(*_args, **kwargs) -> SimpleNamespace:
        return _pi_success_result(
            "no memex change",
            session_name=kwargs["session_name"],
            session_id="native-header-normalization-noop",
        )

    _install_success_runtime(monkeypatch, _prompt)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, workspace_root=tmp_path)

        assert result["status"] == "completed"
        assert result["memex_updated"] is False
        current = db.get_memex(user_id)
        assert current is not None
        assert current["content"] == "canonical body"
        receipt = _latest_receipt()
        assert receipt["status"] == "completed"
        assert receipt["memex_updated"] is False
        assert "memex_version" not in receipt
    finally:
        db.close()


@pytest.mark.parametrize("scenario", ["source_history", "empty_machine", "existing_graph"])
def test_first_run_state_matches_available_history(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
    scenario: str,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    source_counts = {"codex": 7} if scenario == "source_history" else {}
    monkeypatch.setattr(
        pi_synthesis,
        "_discovered_source_file_counts",
        lambda selected_sources: source_counts,
    )
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda *args: {"valid": True, "issues": [], "stats": {}},
    )
    if scenario == "existing_graph":
        _insert_memory(db, "memory-existing", user_id, "existing durable fact")

    prompts: list[str] = []
    timeouts: list[float] = []

    def _prompt(prompt: str, **kwargs) -> SimpleNamespace:
        prompts.append(prompt)
        timeouts.append(kwargs["timeout"])
        if scenario == "source_history":
            memex_path.write_text(
                "No durable user/project memories have been recorded yet.\n",
                encoding="utf-8",
            )
        return _pi_success_result(
            session_name=kwargs.get("session_name"),
            session_id=f"native-first-run-{scenario}",
        )

    _install_success_runtime(monkeypatch, _prompt)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            first_run=True,
            selected_sources=("codex",) if source_counts else (),
            workspace_root=tmp_path,
        )

        first_run_limit = max(float(SYNC_TIMEOUT), float(FIRST_RUN_SYNC_TIMEOUT))
        assert timeouts[0] == first_run_limit
        assert f"- Time limit: {int(first_run_limit):,} s. If the run times out" in prompts[0]
        assert "- Kind: wake" in prompts[0]

        if scenario == "source_history":
            assert "## First run" in prompts[0]
            assert len(prompts) == 4
            assert result["status"] == "failed"
            assert "codex: 7 discovered files/rows" in prompts[0]
            assert "First synthesis produced an empty MEMEX" in str(result["error"])
            assert db.get_memex(user_id) is None
            assert not memex_path.exists()
            assert _latest_receipt()["status"] == "failed"
        elif scenario == "empty_machine":
            assert len(prompts) == 1
            assert result["status"] == "completed"
            memex = db.get_memex(user_id)
            assert memex is not None
            assert "No prior harness history was detected" in memex["content"]
            assert (
                strip_memex_header(memex_path.read_text(encoding="utf-8")).strip()
                == memex["content"]
            )
            assert _latest_receipt()["status"] == "completed"
        else:
            assert len(prompts) == 4
            assert result["status"] == "failed"
            assert "canonical memex is unavailable" in str(result["error"])
            assert db.get_memex(user_id) is None
            assert db.count_memories(user_id) == 1
    finally:
        db.close()


def test_pi_synthesize_records_missing_model_as_blocked(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")

    def _raise_no_model(_model_override=None):
        raise RuntimeError("No Pi model is configured")

    monkeypatch.setattr(pi_synthesis, "resolve_pi_model", _raise_no_model)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            skill_override="test synthesis prompt",
            workspace_root=tmp_path,
            first_run=False,
        )

        assert result["status"] == "blocked"
        assert result["reason"] == "setup_blocked"
        assert "No Pi model is configured" in str(result["error"])

        receipts = list_receipts(_control_dir())
        assert len(receipts) == 1
        assert receipts[0]["status"] == "blocked"
        assert receipts[0]["memex_updated"] is False

    finally:
        db.close()


def test_pi_synthesize_skips_when_synthesis_lock_is_held(db, user_id: str) -> None:
    lock_handle, _ = pi_synthesis._acquire_synthesis_lock(user_id)
    try:
        result = pi_synthesis.pi_synthesize(db, user_id)
    finally:
        pi_synthesis._release_synthesis_lock(lock_handle)

    assert result["status"] == "skipped"
    assert result["reason"] == "locked"
    assert result["memex_updated"] is False
    assert "cycle_runtime" not in result
    assert list_receipts(_control_dir()) == []


def test_pi_synthesize_uses_now_override_for_replay_receipt(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")
    now_override = datetime.fromisoformat("2026-03-07T23:59:00-08:00")

    prompts: list[str] = []

    def _prompt(prompt: str, **kwargs) -> SimpleNamespace:
        prompts.append(prompt)
        return _pi_success_result(
            session_name=kwargs.get("session_name"),
            session_id="native-session-time",
        )

    _install_success_runtime(monkeypatch, _prompt)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, now_override=now_override)

        assert result["status"] == "completed"
        receipt = _latest_receipt()
        assert receipt["started_at"] == "2026-03-07T23:59:00-08:00"
        assert receipt["completed_at"] == "2026-03-07T23:59:00-08:00"
        assert result["session_id"] == "native-session-time"
        # A replay renders as an ordinary wake anchored on the supplied time.
        reference = format_now_for_prompt(now_override)
        assert (
            f"- Now: {reference}. Use this, not the system clock, for today and for "
            "relative dates." in prompts[0]
        )
        run_block = prompts[0][prompts[0].index("# This run") :]
        assert "- Kind: wake" in run_block
        assert "replay" not in run_block.lower()
    finally:
        db.close()


def test_pi_synthesize_marks_replay_db_validation_issue_failed(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")

    _install_success_runtime(monkeypatch, lambda *args, **kwargs: _pi_success_result())
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda *args: {
            "valid": False,
            "issues": ["syke.db read error: database disk image is malformed"],
            "stats": {"syke_db_path": str(tmp_path / "syke.db")},
        },
    )

    try:
        result = pi_synthesis.pi_synthesize(db, user_id)

        assert result["status"] == "failed"
        assert "Cycle DB validation failed" in str(result["error"])
        assert result["validation"]["issues"] == [
            "syke.db read error: database disk image is malformed"
        ]
        receipt = _latest_receipt()
        assert receipt["status"] == "failed"
        assert receipt["memex_updated"] is False
    finally:
        db.close()


def test_pi_synthesize_restores_recovery_point_when_semantic_gate_fails(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    update_memex(db, user_id, "canonical memex")
    for index in range(6):
        _insert_memory(
            db,
            f"mem-collapse-{index}",
            user_id,
            f"Durable memory {index}",
        )
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda *args: {"valid": True, "issues": [], "stats": {}},
    )

    prompts: list[tuple[str, dict[str, object]]] = []

    def _prompt(prompt: str, **kwargs) -> SimpleNamespace:
        prompts.append((prompt, kwargs))
        db.conn.execute("DROP TRIGGER protect_memories_stable_fields")
        db.conn.execute(
            "UPDATE memories SET content = content || ' collapsed' WHERE user_id = ?",
            (user_id,),
        )
        db.conn.execute(
            "UPDATE memories SET created_at = '1900-01-01T00:00:00+00:00' WHERE user_id = ?",
            (user_id,),
        )
        db.conn.commit()
        return _pi_success_result("collapsed memories")

    _install_success_runtime(monkeypatch, _prompt)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, workspace_root=tmp_path)

        assert result["status"] == "failed"
        assert "semantic gate failed" in str(result["error"])
        restored_rows = db.conn.execute(
            "SELECT id, content FROM memories WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        assert [row["content"] for row in restored_rows] == [
            f"Durable memory {index}" for index in range(6)
        ]
        receipt = _latest_receipt()
        assert receipt["status"] == "failed"
        assert receipt["memex_updated"] is False
        assert result["recovery"]["restored"] is True
        assert result["acceptance"]["repair_prompts"] == 3
        assert result["acceptance"]["accepted_attempt"] is None
        assert len(result["acceptance"]["rejections"]) == 4
        assert len(prompts) == 4
        assert prompts[0][1]["new_session"] is True
        assert all(prompt_kwargs["new_session"] is False for _, prompt_kwargs in prompts[1:])
    finally:
        db.close()


def test_pi_synthesize_repairs_malformed_search_index_during_cycle(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "syke.db"
    db = SykeDB(db_path)
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")
    monkeypatch.setattr(pi_synthesis, "SYKE_DB", db_path)
    update_memex(db, user_id, "canonical memex")
    _insert_memory(
        db,
        "mem-search-cache",
        user_id,
        "Searchable quantum memory",
    )

    def _prompt(*args, **kwargs) -> SimpleNamespace:
        _corrupt_search_index(db)
        return _pi_success_result("corrupted derived search cache")

    _install_success_runtime(monkeypatch, _prompt)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, workspace_root=tmp_path)

        assert result["status"] == "completed"
        assert result["validation"]["valid"] is True
        assert result["semantic_gate"]["valid"] is True
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = db.conn.execute(
            """SELECT fts.memory_id
               FROM memories_fts fts
               JOIN memories m ON m.id = fts.memory_id
               WHERE memories_fts MATCH ?
                 AND m.user_id = ?""",
            ("quantum", user_id),
        ).fetchall()
        assert [row["memory_id"] for row in rows] == ["mem-search-cache"]
    finally:
        db.close()


def test_pi_synthesize_repair_deadline_is_wall_clock(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The cycle deadline must be wall time, not sleep-frozen monotonic time.

    After a system sleep the monotonic clock barely advanced, so the old
    monotonic deadline let repair prompts continue long past the intended
    budget. Here the wall clock jumps past the deadline between attempts;
    the cycle must refuse the next repair prompt instead of continuing.
    """
    db = SykeDB(tmp_path / "syke.db")
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", tmp_path / "MEMEX.md")
    update_memex(db, user_id, "canonical memex")
    _insert_memory(db, "mem-wallclock", user_id, "must remain current")

    prompts: list[tuple[str, dict[str, object]]] = []

    def _prompt(prompt: str, **kwargs) -> SimpleNamespace:
        prompts.append((prompt, kwargs))
        if len(prompts) == 1:
            # Sabotage the DB so acceptance rejects attempt 1 and forces a
            # repair-path evaluation of the deadline.
            db.conn.execute("DROP TRIGGER protect_memories_stable_fields")
            db.conn.execute(
                "UPDATE memories SET content = 'unaccepted revision' WHERE id = 'mem-wallclock'"
            )
            db.conn.commit()
            # Simulate a wake: wall clock jumps past the cycle deadline
            # while the monotonic clock barely moved (sleep).
            state["now"] += 1_000.0
            return _pi_success_result("unaccepted answer")
        return _pi_success_result("should never be reached")

    _install_success_runtime(monkeypatch, _prompt)

    real_time = time.time
    real_monotonic = time.monotonic
    state = {"now": real_time()}

    def fake_time() -> float:
        return state["now"]

    monkeypatch.setattr(
        pi_synthesis,
        "time",
        SimpleNamespace(time=fake_time, monotonic=real_monotonic),
    )

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            workspace_root=tmp_path,
            timeout_override=300.0,
        )
    finally:
        state["now"] = real_time()

    assert result["status"] == "failed"
    assert "deadline expired" in str(result.get("error"))
    # Only the first attempt ran; the expired wall deadline blocked repair.
    assert len(prompts) == 1
