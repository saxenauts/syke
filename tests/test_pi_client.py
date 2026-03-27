from __future__ import annotations

import io
from pathlib import Path

from syke.llm import pi_client
from syke.llm.pi_client import RpcEventStream, build_transcript_from_messages


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
    assert stream.get_tool_invocations() == [{"name": "grep", "input": {"pattern": "memex"}, "id": None}]


def test_rpc_stream_normalizes_tool_invocations_without_double_counting_end_events() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "toolcall_start",
                    "toolCall": {
                        "id": "call_1",
                        "toolName": "bash",
                        "input": {"command": "pwd"},
                    },
                },
            },
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "toolcall_end",
                    "toolCall": {
                        "id": "call_1",
                        "toolName": "bash",
                        "input": {"command": "pwd"},
                    },
                },
            },
        ]
    )

    assert stream.get_tool_invocations() == [
        {"name": "bash", "input": {"command": "pwd"}, "id": "call_1"}
    ]


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
                    "stopReason": "stop",
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
        "stop_reason": "stop",
    }


def test_rpc_stream_extracts_tool_invocations_from_full_message_blocks() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call_1",
                            "name": "read",
                            "arguments": {"path": "memex.md"},
                        },
                        {
                            "type": "toolCall",
                            "id": "call_2",
                            "name": "bash",
                            "arguments": {"command": "sqlite3 events.db '.tables'"},
                        },
                    ],
                },
            }
        ]
    )

    assert stream.get_tool_invocations() == [
        {"name": "read", "input": {"path": "memex.md"}, "id": "call_1"},
        {"name": "bash", "input": {"command": "sqlite3 events.db '.tables'"}, "id": "call_2"},
    ]


def test_build_transcript_from_messages_normalizes_assistant_and_tool_results() -> None:
    transcript = build_transcript_from_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "inspect"},
                    {"type": "toolCall", "id": "call_1", "name": "bash", "arguments": {"command": "pwd"}},
                    {"type": "text", "text": "done"},
                ],
            },
            {
                "role": "toolResult",
                "toolCallId": "call_1",
                "toolName": "bash",
                "content": [{"type": "text", "text": "/tmp"}],
                "isError": False,
            },
        ]
    )

    assert transcript == [
        {
            "role": "assistant",
            "blocks": [
                {"type": "thinking", "text": "inspect"},
                {"type": "tool_use", "name": "bash", "input": {"command": "pwd"}},
                {"type": "text", "text": "done"},
            ],
        },
        {
            "role": "user",
            "blocks": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "tool_name": "bash",
                    "content": "/tmp",
                    "is_error": False,
                }
            ],
        },
    ]


def test_ensure_pi_binary_writes_stable_launcher_from_existing_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    pi_home = tmp_path / "syke-home"
    pi_bin = pi_home / "bin" / "pi"
    pi_node = pi_home / "bin" / "node"
    pi_prefix = pi_home / "pi"
    pi_cli = pi_prefix / "node_modules" / "@mariozechner" / "pi-coding-agent" / "dist" / "cli.js"
    real_node = tmp_path / "real-node"

    pi_cli.parent.mkdir(parents=True, exist_ok=True)
    pi_cli.write_text("console.log('pi');", encoding="utf-8")
    real_node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real_node.chmod(0o755)
    pi_bin.parent.mkdir(parents=True, exist_ok=True)
    pi_bin.symlink_to(pi_cli)

    monkeypatch.setattr(pi_client, "PI_LOCAL_PREFIX", pi_prefix)
    monkeypatch.setattr(pi_client, "PI_PACKAGE_ROOT", pi_cli.parent.parent)
    monkeypatch.setattr(pi_client, "PI_CLI_JS", pi_cli)
    monkeypatch.setattr(pi_client, "PI_BIN", pi_bin)
    monkeypatch.setattr(pi_client, "PI_NODE_BIN", pi_node)
    monkeypatch.setattr(pi_client, "_NODE_CANDIDATES", [])
    monkeypatch.setattr(pi_client, "_NPM_CANDIDATES", [])
    monkeypatch.setattr(pi_client.shutil, "which", lambda name: str(real_node) if name == "node" else None)

    launcher = Path(pi_client.ensure_pi_binary())

    assert launcher == pi_bin
    assert launcher.exists()
    assert not launcher.is_symlink()
    assert pi_node.is_symlink()
    launcher_text = launcher.read_text(encoding="utf-8")
    assert str(pi_node) in launcher_text
    assert str(pi_cli) in launcher_text
    assert pi_cli.read_text(encoding="utf-8") == "console.log('pi');"


def test_get_pi_version_uses_launcher_in_minimal_env(tmp_path: Path, monkeypatch) -> None:
    pi_home = tmp_path / "syke-home"
    pi_bin = pi_home / "bin" / "pi"
    pi_node = pi_home / "bin" / "node"
    pi_prefix = pi_home / "pi"
    pi_cli = pi_prefix / "node_modules" / "@mariozechner" / "pi-coding-agent" / "dist" / "cli.js"
    real_node = tmp_path / "real-node"

    pi_cli.parent.mkdir(parents=True, exist_ok=True)
    pi_cli.write_text("console.log('pi');", encoding="utf-8")
    real_node.write_text("#!/bin/sh\necho vtest >&2\n", encoding="utf-8")
    real_node.chmod(0o755)

    monkeypatch.setattr(pi_client, "PI_LOCAL_PREFIX", pi_prefix)
    monkeypatch.setattr(pi_client, "PI_PACKAGE_ROOT", pi_cli.parent.parent)
    monkeypatch.setattr(pi_client, "PI_CLI_JS", pi_cli)
    monkeypatch.setattr(pi_client, "PI_BIN", pi_bin)
    monkeypatch.setattr(pi_client, "PI_NODE_BIN", pi_node)
    monkeypatch.setattr(pi_client, "_NODE_CANDIDATES", [])
    monkeypatch.setattr(pi_client, "_NPM_CANDIDATES", [])
    monkeypatch.setattr(pi_client.shutil, "which", lambda name: str(real_node) if name == "node" else None)

    pi_client.ensure_pi_binary()
    assert pi_client.get_pi_version(minimal_env=True) == "vtest"


def test_new_session_uses_rpc_request(tmp_path: Path, monkeypatch) -> None:
    runtime = pi_client.PiRuntime(workspace_dir=tmp_path)
    seen: dict[str, object] = {}

    def fake_send_request(command: dict[str, object], *, timeout: float = 30.0) -> dict[str, object]:
        seen["command"] = command
        seen["timeout"] = timeout
        return {"cancelled": False}

    monkeypatch.setattr(runtime, "_send_request", fake_send_request)

    response = runtime.new_session(timeout=12.5)

    assert response == {"cancelled": False}
    assert seen == {"command": {"type": "new_session"}, "timeout": 12.5}
