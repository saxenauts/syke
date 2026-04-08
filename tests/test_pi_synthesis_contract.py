from __future__ import annotations

import io
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import syke.runtime as runtime_module
from syke.db import SykeDB
from syke.llm import pi_client
from syke.llm.backends import pi_synthesis
from syke.memory.memex import update_memex
from syke.models import Event


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
    db.insert_event(
        Event(
            id="evt-001",
            user_id=user_id,
            source="codex",
            timestamp=datetime(2026, 4, 4, 0, 0, tzinfo=UTC),
            event_type="turn",
            title="hello",
            content="world",
            metadata={},
            external_id="codex:1",
        )
    )
    db.insert_event(
        Event(
            id="evt-002",
            user_id=user_id,
            source="codex",
            timestamp=datetime(2026, 4, 4, 0, 1, tzinfo=UTC),
            event_type="turn",
            title="followup",
            content="world",
            metadata={},
            external_id="codex:2",
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
            time.sleep(0.01)
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
            time.sleep(0.01)
            stream._events.append(
                {
                    "type": "auto_retry_start",
                    "attempt": 1,
                    "maxAttempts": 3,
                    "delayMs": 2000,
                    "errorMessage": '429 {"error":{"type":"rate_limit_error","message":"busy"}}',
                }
            )
            time.sleep(0.01)
            stream._events.append({"type": "auto_retry_end", "success": True, "attempt": 1})
            time.sleep(0.01)
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
            "SELECT status, cursor_end, events_processed FROM cycle_records WHERE user_id = ? ORDER BY started_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        assert latest_cycle["status"] == "completed"
        assert latest_cycle["cursor_end"] is not None
    finally:
        db.close()
