from __future__ import annotations

import json
from datetime import UTC, datetime

from syke.db import SykeDB
from syke.trace_store import persist_rollout_trace


def test_persist_rollout_trace_writes_canonical_json(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SYKE_DB", str(tmp_path / "trace.db"))
    with SykeDB(tmp_path / "trace.db") as db:
        run_id = persist_rollout_trace(
            db=db,
            user_id="user",
            run_id="run-123",
            kind="ask",
            started_at=datetime(2026, 4, 7, 22, 0, tzinfo=UTC),
            completed_at=datetime(2026, 4, 7, 22, 1, tzinfo=UTC),
            status="completed",
            input_text="what changed?",
            output_text="answer",
            thinking=["step 1", "step 2"],
            transcript=[{"role": "assistant", "blocks": [{"type": "text", "text": "answer"}]}],
            tool_calls=[{"name": "bash", "input": {"command": "pwd"}}],
            event_count=7,
            metrics={"duration_ms": 1000, "cost_usd": 0.1},
            runtime={"provider": "openai", "model": "gpt-test", "transport": "daemon_ipc"},
            extras={"transport": "daemon_ipc"},
        )

        assert run_id == "run-123"
        row = db.conn.execute(
            "SELECT * FROM rollout_traces WHERE id = ?",
            ("run-123",),
        ).fetchone()

    assert row is not None
    payload = dict(row)
    assert payload["kind"] == "ask"
    assert payload["id"] == "run-123"
    assert payload["input_text"] == "what changed?"
    assert payload["output_text"] == "answer"
    assert json.loads(payload["thinking"]) == ["step 1", "step 2"]
    assert payload["event_count"] == 7
    assert payload["duration_ms"] == 1000
    assert payload["provider"] == "openai"
    assert payload["transport"] == "daemon_ipc"
