from __future__ import annotations

import io

from syke.llm.pi_client import RpcEventStream


def _stream_with_events(events: list[dict]) -> RpcEventStream:
    stream = RpcEventStream(io.StringIO(""))
    stream._events = events  # test helper
    return stream


def test_rpc_stream_extracts_text_thinking_and_tool_calls() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "considering"},
            },
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "hello "},
            },
            {
                "type": "tool_execution_start",
                "toolExecution": {"name": "grep", "input": {"pattern": "memex"}},
            },
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "world"},
            },
        ]
    )

    assert stream.get_output() == "hello world"
    assert stream.get_thinking_chunks() == ["considering"]
    assert len(stream.get_tool_calls()) == 1


def test_rpc_stream_extracts_usage_from_latest_assistant_message() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_end",
                    "partial": {
                        "role": "assistant",
                        "content": "done",
                        "usage": {
                            "input": 123,
                            "output": 45,
                            "cacheRead": 6,
                            "cacheWrite": 7,
                        },
                        "cost": {"total": 0.0123},
                    },
                },
            }
        ]
    )

    usage = stream.get_usage()
    assert usage["input_tokens"] == 123
    assert usage["output_tokens"] == 45
    assert usage["cache_read_tokens"] == 6
    assert usage["cache_write_tokens"] == 7
    assert usage["cost_usd"] == 0.0123


def test_rpc_stream_extracts_output_usage_and_metadata_from_assistant_message_events() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message_update",
                "message": {
                    "role": "assistant",
                    "provider": "azure-openai-responses",
                    "model": "gpt-5.4-mini",
                    "responseId": "resp_123",
                    "content": [{"type": "text", "text": "hello"}],
                    "usage": {
                        "input": 10,
                        "output": 2,
                        "cacheRead": 1,
                        "cacheWrite": 0,
                        "cost": {"total": 0.001},
                    },
                },
                "assistantMessageEvent": {"type": "text_delta", "delta": "hello"},
            }
        ]
    )

    assert stream.get_output() == "hello"
    assert stream.get_usage()["cost_usd"] == 0.001
    assert stream.get_message_metadata() == {
        "provider": "azure-openai-responses",
        "model": "gpt-5.4-mini",
        "response_id": "resp_123",
    }
