from __future__ import annotations

from pathlib import Path

import pytest

import syke.llm.backends.pi_ask as pi_ask_module
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
