from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from syke.llm import pi_client
from syke.llm.pi_rpc import RpcEventStream


def _make_runtime(
    tmp_path: Path,
    monkeypatch,
    *,
    provider: str = "zai",
    model: str = "glm-5",
) -> pi_client.PiRuntime:
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(
            provider=provider,
            model=model_override or model,
        ),
    )
    return pi_client.PiRuntime(
        workspace_dir=tmp_path,
        session_dir=tmp_path.with_name(f"{tmp_path.name}-sessions"),
        model=model,
    )


def _stream_with_events(events: list[dict]) -> RpcEventStream:
    stream = RpcEventStream(io.StringIO(""))
    stream._events = events  # test helper
    return stream


def test_rpc_stream_projects_final_output_tools_usage_and_metadata() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "considering"},
            },
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "intermediate"},
            },
            {
                "type": "tool_execution_start",
                "toolCallId": "call_1",
                "toolName": "grep",
                "args": {"pattern": "memex"},
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "responseId": "resp_123",
                    "stopReason": "stop",
                    "content": [
                        {"type": "text", "text": "Final answer."},
                        {
                            "type": "toolCall",
                            "id": "call_1",
                            "name": "grep",
                            "arguments": {"pattern": "memex"},
                        },
                        {
                            "type": "toolCall",
                            "id": "call_2",
                            "name": "read",
                            "arguments": {"path": "MEMEX.md"},
                        },
                    ],
                    "usage": {
                        "input": 123,
                        "output": 45,
                        "cacheRead": 6,
                        "cacheWrite": 7,
                        "cost": {"total": 0.0123},
                    },
                },
            },
        ]
    )

    assert stream.get_output() == "Final answer."
    assert stream.get_thinking_chunks() == ["considering"]
    assert stream.get_tool_invocations() == [
        {"name": "grep", "input": {"pattern": "memex"}, "id": "call_1"},
        {"name": "read", "input": {"path": "MEMEX.md"}, "id": "call_2"},
    ]
    assert stream.get_usage() == {
        "input_tokens": 123,
        "output_tokens": 45,
        "cache_read_tokens": 6,
        "cache_write_tokens": 7,
        "cost_usd": 0.0123,
    }
    assert stream.get_message_metadata() == {
        "provider": "openai",
        "model": "gpt-5.4",
        "response_id": "resp_123",
        "stop_reason": "stop",
    }


def test_rpc_stream_sums_each_final_assistant_message_once() -> None:
    first_message = {
        "role": "assistant",
        "responseId": "response-1",
        "usage": {
            "input": 100,
            "output": 20,
            "cacheRead": 10,
            "cacheWrite": 2,
            "cost": {"total": 0.02},
        },
    }
    stream = _stream_with_events(
        [
            {"type": "message_end", "message": first_message},
            {"type": "turn_end", "message": first_message, "toolResults": []},
            {"type": "agent_end", "messages": [first_message]},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "responseId": "response-2",
                    "usage": {
                        "input": 30,
                        "output": 5,
                        "cacheRead": 2,
                        "cacheWrite": 1,
                        "cost": {"total": 0.005},
                    },
                },
            },
            {
                "type": "message_end",
                "message": {"role": "assistant", "responseId": "response-3"},
            },
        ]
    )

    assert stream.get_usage() == {
        "input_tokens": 130,
        "output_tokens": 25,
        "cache_read_tokens": 12,
        "cache_write_tokens": 3,
        "cost_usd": 0.025,
    }


def test_rpc_stream_does_not_finish_on_agent_end() -> None:
    event = {"type": "agent_end", "messages": [], "willRetry": True}
    stream = RpcEventStream(io.StringIO(f"{json.dumps(event)}\n"))

    stream.start()
    stream._thread.join(timeout=1.0)

    assert stream.wait(timeout=0) is False


def test_rpc_stream_finishes_only_after_agent_settled() -> None:
    events = [
        {"type": "agent_end", "messages": [], "willRetry": True},
        {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3, "delayMs": 1},
        {
            "type": "agent_end",
            "willRetry": False,
            "messages": [
                {
                    "role": "assistant",
                    "provider": "kimi-coding",
                    "model": "k2p5",
                    "responseId": "resp_final",
                    "stopReason": "stop",
                    "content": [{"type": "text", "text": "done"}],
                }
            ],
        },
        {"type": "agent_settled"},
    ]
    stream = RpcEventStream(io.StringIO("".join(f"{json.dumps(event)}\n" for event in events)))

    stream.start()

    assert stream.wait(timeout=1.0) is True
    assert stream.get_assistant_error() is None
    assert stream.get_message_metadata()["response_id"] == "resp_final"


def test_prompt_projects_stream_tools_native_turns_and_optional_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)

    class _FakeStream:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "tool_execution_start",
                    "toolCallId": "call_1",
                    "toolName": "bash",
                    "args": {"command": "pwd"},
                }
            ]
            self.error = None

        def set_callback(self, callback) -> None:
            self.callback = callback

        def reset(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> bool:
            return True

        def get_output(self) -> str:
            return "done"

        def get_thinking_chunks(self) -> list[str]:
            return []

        def get_usage(self) -> dict[str, int | float | None]:
            return {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.001,
            }

        def get_message_metadata(self) -> dict[str, str | None]:
            return {
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "response_id": "resp_123",
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return [{"name": "bash", "input": {"command": "pwd"}, "id": "call_1"}]

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 2})
    result = runtime.prompt("What happened?", timeout=5)

    assert result.status == "completed"
    assert result.output == "done"
    assert result.tool_calls == [{"name": "bash", "input": {"command": "pwd"}, "id": "call_1"}]
    assert result.num_turns == 2
    assert result.response_id == "resp_123"
    assert result.stop_reason is None


class _SettleStream:
    """Fake stream whose Nth settle ends with the Nth scripted outcome."""

    def __init__(self, outcomes: list[str | None], *, stream_error: str | None = None) -> None:
        self.outcomes = outcomes
        self.settles = 0
        self.error = stream_error
        self.events: list[dict] = []

    def set_callback(self, callback) -> None:
        return None

    def reset(self) -> None:
        return None

    def rearm(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> bool:
        self.settles += 1
        return True

    def get_assistant_error(self) -> str | None:
        return self.outcomes[min(self.settles, len(self.outcomes)) - 1]

    def get_output(self) -> str:
        return "" if self.get_assistant_error() else "done"

    def get_thinking_chunks(self) -> list[str]:
        return []

    def get_usage(self) -> dict[str, int | float | None]:
        return {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.001,
        }

    def get_message_metadata(self) -> dict[str, str | None]:
        return {"provider": "p", "model": "m", "response_id": None, "stop_reason": None}

    def get_tool_invocations(self) -> list[dict[str, object]]:
        return []


def _prompt_with_stream(tmp_path: Path, monkeypatch, stream: _SettleStream):
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)
    runtime._stream = stream
    sent: list[dict] = []
    monkeypatch.setattr(runtime, "_send", sent.append)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {})
    return runtime.prompt("What happened?", timeout=60), sent


def test_prompt_retries_a_failed_provider_call_and_continues(tmp_path: Path, monkeypatch) -> None:
    result, sent = _prompt_with_stream(
        tmp_path, monkeypatch, _SettleStream(["400: upstream refused", None])
    )

    assert result.status == "completed"
    assert result.output == "done"
    assert [payload["message"] for payload in sent] == [
        "What happened?",
        pi_client.PROVIDER_ERROR_RETRY_PROMPT,
    ]


def test_prompt_stops_retrying_after_the_retry_limit(tmp_path: Path, monkeypatch) -> None:
    stream = _SettleStream(["400: upstream refused"])
    result, sent = _prompt_with_stream(tmp_path, monkeypatch, stream)

    assert result.status == "error"
    assert result.error == "400: upstream refused"
    assert len(sent) == 1 + pi_client.PROVIDER_ERROR_RETRIES


def test_prompt_does_not_retry_when_pi_itself_failed(tmp_path: Path, monkeypatch) -> None:
    stream = _SettleStream(["400: upstream refused"], stream_error="Pi command failed")
    result, sent = _prompt_with_stream(tmp_path, monkeypatch, stream)

    assert result.status == "error"
    assert len(sent) == 1
