from __future__ import annotations

import io
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
    monkeypatch.setattr(pi_synthesis, "_validate_cycle_output", lambda: {"valid": True, "issues": [], "stats": {}})
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
