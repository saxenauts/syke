from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import syke.llm.backends.pi_ask as pi_ask_module
from syke.db import SykeDB
from syke.llm.pi_client import PiCycleResult
from syke.runtime import workspace as workspace_module


class _FakeRuntime:
    is_alive = True

    def __init__(self, result: PiCycleResult, workspace_root: Path):
        self._result = result
        self._workspace_root = workspace_root

    def status(self) -> dict[str, object]:
        return {"workspace": str(self._workspace_root), "pid": 1234}

    def prompt(self, *_args: object, **_kwargs: object) -> PiCycleResult:
        return self._result


def test_pi_ask_preserves_capture_trace_on_non_ok_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    session_dir = tmp_path / "sessions"
    workspace_root.mkdir(exist_ok=True)
    session_dir.mkdir(exist_ok=True)

    result = PiCycleResult(
        status="failed",
        output="Request timed out.",
        thinking=["waiting"],
        tool_calls=[{"name": "read", "input": {"path": "packet.json"}}],
        events=[],
        transcript=[{"role": "assistant", "content": "partial"}],
        num_turns=1,
        duration_ms=32928,
        input_tokens=10,
        output_tokens=0,
        cache_read_tokens=None,
        cache_write_tokens=None,
        cost_usd=0,
        provider=None,
        response_model=None,
        response_id=None,
        stop_reason=None,
        error="Request timed out.",
    )
    runtime = _FakeRuntime(result, workspace_root)

    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace_module, "SESSIONS_DIR", session_dir)
    monkeypatch.setattr(
        pi_ask_module,
        "get_pi_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("no runtime")),
    )
    monkeypatch.setattr(pi_ask_module, "start_pi_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr("syke.source_selection.get_selected_sources", lambda _user_id: [])

    answer, metadata = pi_ask_module.pi_ask(
        db=object(),  # not used for benchmark transport
        user_id="user",
        question="remember yesterday",
        transport="benchmark",
        capture_trace=True,
    )

    assert answer == "Request timed out."
    assert metadata["error"] == "Request timed out."
    assert metadata["_input_text"] == "remember yesterday"

    trace = metadata["_trace_payload"]
    assert isinstance(trace, dict)
    assert trace["status"] == "failed"
    assert trace["error"] == "Request timed out."
    assert trace["output_text"] == "Request timed out."
    assert trace["tool_calls_detail"] == [{"name": "read", "input": {"path": "packet.json"}}]


def test_pi_ask_pauses_replay_db_connection_during_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    session_dir = tmp_path / "sessions"
    workspace_root.mkdir(exist_ok=True)
    session_dir.mkdir(exist_ok=True)

    db = SykeDB(tmp_path / "syke.db")
    user_id = "user"

    def _prompt(*_args: object, **_kwargs: object) -> PiCycleResult:
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
                "agent wrote while parent ask connection was paused",
                "[]",
                "2026-03-08T00:00:00Z",
                "2026-03-08T00:00:00Z",
                1,
            ),
        )
        external.commit()
        external.close()
        return PiCycleResult(
            status="completed",
            output="done",
            thinking=[],
            tool_calls=[],
            events=[],
            transcript=[{"role": "assistant", "content": "done"}],
            num_turns=1,
            duration_ms=5,
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=0.0,
            provider="kimi-coding",
            response_model="k2p5",
            response_id="resp_pause_db",
            stop_reason="stop",
            error=None,
        )

    runtime = SimpleNamespace(
        is_alive=True,
        prompt=_prompt,
        status=lambda: {"workspace": str(workspace_root), "pid": 1},
    )

    monkeypatch.setenv("SYKE_REPLAY_PAUSE_DB_CONNECTION_DURING_PI", "1")
    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(workspace_module, "SESSIONS_DIR", session_dir)
    monkeypatch.setattr(
        pi_ask_module,
        "get_pi_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("no runtime")),
    )
    monkeypatch.setattr(pi_ask_module, "start_pi_runtime", lambda **_kwargs: runtime)
    monkeypatch.setattr("syke.source_selection.get_selected_sources", lambda _user_id: [])

    try:
        answer, metadata = pi_ask_module.pi_ask(
            db=db,
            user_id=user_id,
            question="what happened?",
            transport="replay-integrated",
            capture_trace=True,
        )

        assert answer == "done"
        assert metadata["error"] is None
        count = db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id = 'agent-memory'"
        ).fetchone()[0]
        trace_count = db.conn.execute(
            "SELECT COUNT(*) FROM rollout_traces WHERE kind = 'ask' AND user_id = ?",
            (user_id,),
        ).fetchone()[0]
        assert count == 1
        assert trace_count == 1
    finally:
        db.close()
