from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import syke.runtime as runtime_module
from syke.db import SykeDB
from syke.llm import pi_client
from syke.llm.backends import pi_synthesis
from syke.memory.memex import update_memex
from syke.models import Memory


def test_sync_memex_prefers_canonical_db_over_stale_artifact(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "prior memex")
    update_memex(db, user_id, "canonical db memex")
    memex_path.write_text("stale artifact memex\n", encoding="utf-8")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="prior memex",
        previous_artifact_content="stale artifact memex",
    )

    assert result == {
        "ok": True,
        "updated": True,
        "source": "db",
        "artifact_written": True,
    }
    assert db.get_memex(user_id)["content"] == "canonical db memex"
    written = memex_path.read_text(encoding="utf-8")
    assert "canonical db memex" in written
    assert written.startswith("# MEMEX [")  # fill indicator header
    assert "/ 2,000 tokens" in written


def test_sync_memex_imports_artifact_when_db_did_not_change(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "prior memex")
    memex_path.write_text("artifact memex\n", encoding="utf-8")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="prior memex",
        previous_artifact_content=None,
    )

    assert result["ok"] is True
    assert result["updated"] is True
    assert result["source"] == "artifact"
    assert db.get_memex(user_id)["content"] == "artifact memex"
    written = memex_path.read_text(encoding="utf-8")
    assert "artifact memex" in written


def test_sync_memex_accepts_projected_body_with_trailing_newline(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "prior memex")
    update_memex(db, user_id, "canonical db memex\n")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="prior memex",
        previous_artifact_content=None,
    )

    assert result["ok"] is True
    assert result["updated"] is True
    assert result["source"] == "db"
    written = memex_path.read_text(encoding="utf-8")
    assert written.startswith("# MEMEX [")
    assert pi_synthesis._strip_memex_header(written).strip() == "canonical db memex"


def test_sync_memex_versions_in_place_db_mutation(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    old_id = update_memex(db, user_id, "old memex")
    old_row = db.get_memex(user_id)
    assert old_row is not None
    db.update_memory(user_id, old_id, "agent mutated active row in place")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="old memex",
        previous_id=old_id,
        previous_updated_at=old_row["updated_at"],
        previous_artifact_content=None,
    )

    assert result["ok"] is True
    assert result["updated"] is True
    assert result["source"] == "db"
    assert result["normalized_in_place"] is True
    active = db.get_memex(user_id)
    assert active is not None
    assert active["id"] != old_id
    assert active["content"] == "agent mutated active row in place"
    old = db.get_memory(user_id, old_id)
    assert old is not None
    assert old["active"] == 0
    assert old["content"] == "old memex"
    assert old["superseded_by"] == active["id"]
    written = memex_path.read_text(encoding="utf-8")
    assert "agent mutated active row in place" in written


def test_sync_memex_projects_existing_canonical_memex_without_artifact(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "canonical memex")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="canonical memex",
        previous_artifact_content=None,
    )

    assert result == {
        "ok": True,
        "updated": False,
        "source": "db",
        "artifact_written": True,
    }
    written = memex_path.read_text(encoding="utf-8")
    assert "canonical memex" in written
    assert written.startswith("# MEMEX [")


def test_sync_memex_does_not_import_stale_artifact_when_nothing_changed(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "canonical memex")
    memex_path.write_text("stale artifact memex\n", encoding="utf-8")

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="canonical memex",
        previous_artifact_content="stale artifact memex",
    )

    assert result == {
        "ok": True,
        "updated": False,
        "source": "db",
        "artifact_written": True,
    }
    assert db.get_memex(user_id)["content"] == "canonical memex"
    written = memex_path.read_text(encoding="utf-8")
    assert "canonical memex" in written
    assert written.startswith("# MEMEX [")


def test_sync_memex_restores_previous_when_canonical_row_disappears(
    db,
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)

    update_memex(db, user_id, "canonical memex")
    existing = db.get_memex(user_id)
    assert existing is not None
    memex_path.write_text("canonical memex\n", encoding="utf-8")
    db.conn.execute("UPDATE memories SET active = 0 WHERE id = ?", (existing["id"],))
    db.conn.commit()

    result = pi_synthesis._sync_memex_to_db(
        db,
        user_id,
        previous_content="canonical memex",
        previous_artifact_content="canonical memex",
    )

    assert result == {
        "ok": True,
        "updated": False,
        "source": "previous",
        "artifact_written": True,
    }
    assert db.get_memex(user_id)["content"] == "canonical memex"
    written = memex_path.read_text(encoding="utf-8")
    assert "canonical memex" in written
    assert written.startswith("# MEMEX [")


def test_first_run_rejects_empty_memex_when_sources_have_history(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(
        pi_synthesis,
        "_discovered_source_file_counts",
        lambda selected_sources, *, home=None: {"codex": 7},
    )
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda: {"valid": True, "issues": [], "stats": {}},
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    captured_prompt: list[str] = []

    def _prompt(*args, **kwargs) -> SimpleNamespace:
        captured_prompt.append(args[0])
        memex_path.write_text(
            "As of now:\n- No durable user/project memories have been recorded yet.\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_empty_bootstrap",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=_prompt,
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            first_run=True,
            selected_sources=("codex",),
            workspace_root=tmp_path,
        )

        assert result["status"] == "failed"
        assert captured_prompt
        assert "<first_run_bootstrap>" in captured_prompt[0]
        assert "Use the bootstrap path" in captured_prompt[0]
        assert "codex: 7 discovered files/rows" in captured_prompt[0]
        assert "First synthesis produced an empty MEMEX" in str(result["error"])
        assert "codex=7" in str(result["error"])
        assert result["memex_updated"] is False
        assert db.get_memex(user_id) is None
        assert not memex_path.exists()
        latest_cycle = db._conn.execute(
            "SELECT status, memex_updated FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "failed"
        assert latest_cycle["memex_updated"] == 0
    finally:
        db.close()


def test_first_run_records_empty_memex_when_no_history(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(
        pi_synthesis,
        "_discovered_source_file_counts",
        lambda selected_sources, *, home=None: {},
    )
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda: {"valid": True, "issues": [], "stats": {}},
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_empty_clean_first_run",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        ),
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            first_run=True,
            selected_sources=(),
            workspace_root=tmp_path,
        )

        assert result["status"] == "completed"
        assert result["memex_updated"] is True
        memex = db.get_memex(user_id)
        assert memex is not None
        assert "No durable user/project memories have been captured yet." in memex["content"]
        assert "No prior harness history was detected" in memex["content"]
        written = memex_path.read_text(encoding="utf-8")
        assert "No prior harness history was detected" in written
        latest_cycle = db._conn.execute(
            "SELECT status, memex_updated FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "completed"
        assert latest_cycle["memex_updated"] == 1
    finally:
        db.close()


def test_first_run_still_fails_empty_memex_when_memory_exists(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    db.insert_memory(
        Memory(
            id="mem-existing",
            user_id=user_id,
            content="Existing durable fact that should be synthesized.",
        )
    )
    memex_path = tmp_path / "MEMEX.md"
    monkeypatch.setattr(pi_synthesis, "MEMEX_PATH", memex_path)
    monkeypatch.setattr(
        pi_synthesis,
        "_discovered_source_file_counts",
        lambda selected_sources, *, home=None: {},
    )
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda: {"valid": True, "issues": [], "stats": {}},
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_empty_existing_memory",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        ),
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(
            db,
            user_id,
            first_run=True,
            selected_sources=(),
            workspace_root=tmp_path,
        )

        assert result["status"] == "failed"
        assert "canonical memex is unavailable" in str(result["error"])
        assert db.get_memex(user_id) is None
    finally:
        db.close()


def test_pi_synthesize_blocks_missing_model_before_cycle(
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

        cycle_count = db._conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        assert cycle_count == 1
        latest_cycle = db._conn.execute(
            "SELECT status, memex_updated FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "blocked"
        assert latest_cycle["memex_updated"] == 0

        trace_row = db._conn.execute(
            "SELECT status, error FROM rollout_traces WHERE user_id = ? AND kind = 'synthesis'",
            (user_id,),
        ).fetchone()
        assert trace_row["status"] == "blocked"
        assert "No Pi model is configured" in trace_row["error"]
    finally:
        db.close()


def test_pi_synthesize_skips_when_synthesis_lock_is_held(db, user_id: str) -> None:
    with patch.object(
        pi_synthesis,
        "_acquire_synthesis_lock",
        side_effect=pi_synthesis.SynthesisLockUnavailable("busy"),
    ):
        result = pi_synthesis.pi_synthesize(db, user_id)

    assert result["status"] == "skipped"
    assert result["reason"] == "locked"
    assert result["memex_updated"] is False


def test_pi_synthesize_waits_for_retry_settlement_before_marking_cycle_failed(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")
    # Synthesis doesn't need events — it reads harness data via adapters.
    # Seed a memory so the agent has something to work with.
    db.insert_memory(
        Memory(
            id="mem-seed",
            user_id=user_id,
            content="Seed memory for synthesis test",
        )
    )

    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )
    runtime = pi_client.PiRuntime(workspace_dir=tmp_path, model="k2p5")
    runtime._process = SimpleNamespace(poll=lambda: None, pid=4242)
    runtime._stream = pi_client.RpcEventStream(io.StringIO(""))

    def _send(payload: dict[str, object]) -> None:
        if payload.get("type") != "prompt":
            return

        def _emit() -> None:
            assert runtime._stream is not None
            stream = runtime._stream
            time.sleep(0.1)
            stream._events.append(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "role": "assistant",
                            "provider": "kimi-coding",
                            "model": "k2p5",
                            "responseId": "resp_retryable",
                            "stopReason": "error",
                            "errorMessage": '429 {"error":{"type":"rate_limit_error","message":"busy"}}',
                            "content": [],
                        }
                    ],
                }
            )
            stream._done.set()
            time.sleep(0.1)
            stream._events.append(
                {
                    "type": "auto_retry_start",
                    "attempt": 1,
                    "maxAttempts": 3,
                    "delayMs": 2000,
                    "errorMessage": '429 {"error":{"type":"rate_limit_error","message":"busy"}}',
                }
            )
            time.sleep(0.1)
            stream._events.append({"type": "auto_retry_end", "success": True, "attempt": 1})
            time.sleep(0.1)
            stream._events.append(
                {
                    "type": "agent_end",
                    "messages": [
                        {
                            "role": "assistant",
                            "provider": "kimi-coding",
                            "model": "k2p5",
                            "responseId": "resp_final",
                            "stopReason": "stop",
                            "content": [{"type": "text", "text": "done"}],
                            "usage": {
                                "input": 10,
                                "output": 4,
                                "cacheRead": 2,
                                "cacheWrite": 0,
                                "cost": {"total": 0.0},
                            },
                        }
                    ],
                }
            )
            stream._done.set()

        threading.Thread(target=_emit, daemon=True).start()

    monkeypatch.setattr(runtime, "_send", _send)
    monkeypatch.setattr(runtime, "new_session", lambda timeout=30.0: {})
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 1})
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    )
    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id)

        assert result["status"] == "completed"
        assert result["error"] is None
        assert result["response_id"] == "resp_final"
        assert result["stop_reason"] == "stop"
        latest_cycle = db._conn.execute(
            "SELECT status, cursor_end FROM cycle_records WHERE user_id = ? ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "completed"
        assert latest_cycle["cursor_end"] is not None
    finally:
        db.close()


def test_pi_synthesize_uses_now_override_for_cycle_and_trace_timestamps(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")
    db.insert_memory(
        Memory(
            id="mem-seed-time",
            user_id=user_id,
            content="Seed memory for time override test",
        )
    )

    now_override = datetime.fromisoformat("2026-03-07T23:59:00-08:00")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "syke.trace_store.persist_rollout_trace",
        lambda **kwargs: captured.update(kwargs) or kwargs["run_id"],
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_time",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        ),
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )

    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, now_override=now_override)

        assert result["status"] == "completed"
        latest_cycle = db._conn.execute(
            "SELECT started_at, completed_at FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["started_at"] == "2026-03-07T23:59:00-08:00"
        assert latest_cycle["completed_at"] == "2026-03-07T23:59:00-08:00"
        assert captured["started_at"].isoformat() == "2026-03-07T23:59:00-08:00"
        assert captured["completed_at"].isoformat() == "2026-03-07T23:59:00-08:00"
    finally:
        db.close()


def test_pi_synthesize_records_recovered_memory_touch_count(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")

    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda: {"valid": True, "issues": [], "stats": {}},
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    def _prompt(*args, **kwargs) -> SimpleNamespace:
        db.log_memory_op(
            user_id,
            "synthesis_update",
            memory_ids=["mem_alpha", "__memex__"],
            input_summary="agent touched a route memory",
            output_summary="route memory and memex refreshed",
        )
        return SimpleNamespace(
            ok=True,
            output="Synthesis cycle complete.\n\nUpdated:\n- `mem_beta`\n- `MEMEX.md`",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_touches",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=_prompt,
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, workspace_root=tmp_path)

        assert result["status"] == "completed"
        assert result["memory_touched_count"] == 2
        assert result["memory_touched_ids"] == ["mem_alpha", "mem_beta"]
        latest_cycle = db._conn.execute(
            "SELECT memories_updated, memex_updated FROM cycle_records "
            "WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["memories_updated"] == 2
        assert latest_cycle["memex_updated"] == 0
        trace = db._conn.execute(
            "SELECT extras FROM rollout_traces WHERE user_id = ? AND kind = 'synthesis'",
            (user_id,),
        ).fetchone()
        extras = json.loads(trace["extras"])
        assert extras["memory_touched_count"] == 2
        assert extras["memory_touched_ids"] == ["mem_alpha", "mem_beta"]
    finally:
        db.close()


def test_pi_synthesize_versions_in_place_memex_mutation_before_marking_updated(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    old_id = update_memex(db, user_id, "old canonical memex")

    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda: {"valid": True, "issues": [], "stats": {}},
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    def _prompt(*args, **kwargs) -> SimpleNamespace:
        assert db.update_memory(user_id, old_id, "agent wrote canonical memex in place")
        return SimpleNamespace(
            ok=True,
            output="Updated canonical MEMEX row.",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_memex_in_place",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=_prompt,
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )
    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id, workspace_root=tmp_path)

        assert result["status"] == "completed"
        assert result["memex_updated"] is True
        active = db.get_memex(user_id)
        assert active is not None
        assert active["id"] != old_id
        assert active["content"] == "agent wrote canonical memex in place"
        old = db.get_memory(user_id, old_id)
        assert old is not None
        assert old["active"] == 0
        assert old["content"] == "old canonical memex"
        assert old["superseded_by"] == active["id"]
        latest_cycle = db._conn.execute(
            "SELECT memex_updated FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["memex_updated"] == 1
    finally:
        db.close()


def test_pi_synthesize_marks_post_commit_exception_failed(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")

    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )
    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_commit_fail",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        ),
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )

    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(
        pi_synthesis,
        "_sync_memex_to_db",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("Could not decode to UTF-8 column 'content'")
        ),
    )

    try:
        result = pi_synthesis.pi_synthesize(db, user_id)

        assert result["status"] == "failed"
        assert "Post-synthesis commit failed" in str(result["error"])
        assert "Could not decode to UTF-8" in str(result["error"])
        latest_cycle = db._conn.execute(
            "SELECT status, memex_updated FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "failed"
        assert latest_cycle["memex_updated"] == 0
    finally:
        db.close()


def test_pi_synthesize_marks_replay_db_validation_issue_failed(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")

    monkeypatch.setenv("SYKE_REPLAY_FAIL_ON_DB_VALIDATION", "1")
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )
    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=lambda *args, **kwargs: SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_validation_fail",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        ),
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )

    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(
        pi_synthesis,
        "_validate_cycle_output",
        lambda: {
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
        latest_cycle = db._conn.execute(
            "SELECT status, memex_updated FROM cycle_records WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "failed"
        assert latest_cycle["memex_updated"] == 0
    finally:
        db.close()


def test_pi_synthesize_pauses_replay_db_connection_during_agent(
    user_id: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = SykeDB(tmp_path / "syke.db")
    update_memex(db, user_id, "canonical memex")

    monkeypatch.setenv("SYKE_REPLAY_PAUSE_DB_CONNECTION_DURING_PI", "1")
    monkeypatch.setattr(
        pi_synthesis, "_validate_cycle_output", lambda: {"valid": True, "issues": [], "stats": {}}
    )
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider="kimi-coding",
            model=model_override or "k2p5",
        ),
    )

    def _prompt(*args, **kwargs) -> SimpleNamespace:
        with pytest.raises(sqlite3.ProgrammingError):
            db.conn.execute("SELECT 1")
        external = sqlite3.connect(db.db_path)
        external.execute(
            """INSERT INTO memories
               (id, user_id, content, source_event_ids, created_at, updated_at, active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "agent-memory",
                user_id,
                "agent wrote while parent connection was paused",
                "[]",
                "2026-03-08T00:00:00Z",
                "2026-03-08T00:00:00Z",
                1,
            ),
        )
        external.commit()
        external.close()
        return SimpleNamespace(
            ok=True,
            output="done",
            duration_ms=5,
            cost_usd=0.0,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_pause_db",
            stop_reason="stop",
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
            num_turns=1,
            thinking=[],
        )

    runtime = SimpleNamespace(
        is_alive=True,
        model="k2p5",
        prompt=_prompt,
        status=lambda: {
            "workspace": str(pi_synthesis.WORKSPACE_ROOT),
            "pid": 1,
            "uptime_s": 1,
            "session_count": 1,
        },
    )

    monkeypatch.setattr(
        runtime_module, "get_pi_runtime", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(runtime_module, "start_pi_runtime", lambda **kwargs: runtime)

    try:
        result = pi_synthesis.pi_synthesize(db, user_id)

        assert result["status"] == "completed"
        count = db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id = 'agent-memory'"
        ).fetchone()[0]
        assert count == 1
    finally:
        db.close()
