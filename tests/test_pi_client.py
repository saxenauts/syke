from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from syke.llm import pi_client
from syke.llm.pi_client import RpcEventStream, build_transcript_from_messages


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
    return pi_client.PiRuntime(workspace_dir=tmp_path, model=model)


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
    assert stream.get_tool_invocations() == [
        {"name": "grep", "input": {"pattern": "memex"}, "id": None}
    ]


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


def test_rpc_stream_wait_for_terminal_state_waits_past_retryable_error() -> None:
    stream = RpcEventStream(io.StringIO(""))

    retryable_agent_end = {
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
    final_agent_end = {
        "type": "agent_end",
        "messages": [
            {
                "role": "assistant",
                "provider": "kimi-coding",
                "model": "k2p5",
                "responseId": "resp_final",
                "stopReason": "stop",
                "content": [{"type": "text", "text": "done"}],
                "usage": {"input": 1, "output": 1, "cacheRead": 0, "cacheWrite": 0},
            }
        ],
    }

    def _emit() -> None:
        time.sleep(0.01)
        stream._events.append(retryable_agent_end)
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
        stream._events.append(final_agent_end)
        stream._done.set()

    threading.Thread(target=_emit, daemon=True).start()

    assert stream.wait_for_terminal_state(timeout=0.2) is True
    assert stream.get_assistant_error() is None
    assert stream.get_message_metadata()["response_id"] == "resp_final"


def test_rpc_stream_wait_for_terminal_state_returns_final_retry_failure() -> None:
    stream = RpcEventStream(io.StringIO(""))

    retryable_agent_end = {
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

    def _emit() -> None:
        time.sleep(0.01)
        stream._events.append(retryable_agent_end)
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
        stream._events.append(
            {
                "type": "auto_retry_end",
                "success": False,
                "attempt": 3,
                "finalError": '429 {"error":{"type":"rate_limit_error","message":"busy"}}',
            }
        )
        stream._done.set()

    threading.Thread(target=_emit, daemon=True).start()

    assert stream.wait_for_terminal_state(timeout=0.2) is True
    assert stream.latest_retry_terminal_error() == (
        '429 {"error":{"type":"rate_limit_error","message":"busy"}}'
    )


def test_rpc_stream_wait_for_terminal_state_settles_retryable_error_without_retry_events() -> None:
    stream = RpcEventStream(io.StringIO(""))
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

    assert stream.wait_for_terminal_state(timeout=0.3) is True
    assert stream.latest_retry_terminal_error() is None
    assert stream.get_assistant_error() == '429 {"error":{"type":"rate_limit_error","message":"busy"}}'


def test_rpc_stream_prefers_final_assistant_message_over_intermediate_text_deltas() -> None:
    stream = _stream_with_events(
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "Let me check. "},
            },
            {
                "type": "message_update",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Final answer."}],
                },
                "assistantMessageEvent": {"type": "text_delta", "delta": "Final answer."},
            },
        ]
    )

    assert stream.get_output() == "Final answer."


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
                            "arguments": {"path": "MEMEX.md"},
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
        {"name": "read", "input": {"path": "MEMEX.md"}, "id": "call_1"},
        {"name": "bash", "input": {"command": "sqlite3 events.db '.tables'"}, "id": "call_2"},
    ]


def test_build_transcript_from_messages_normalizes_assistant_and_tool_results() -> None:
    transcript = build_transcript_from_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "inspect"},
                    {
                        "type": "toolCall",
                        "id": "call_1",
                        "name": "bash",
                        "arguments": {"command": "pwd"},
                    },
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
    monkeypatch.setattr(
        pi_client.shutil, "which", lambda name: str(real_node) if name == "node" else None
    )

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
    monkeypatch.setattr(
        pi_client.shutil, "which", lambda name: str(real_node) if name == "node" else None
    )

    pi_client.ensure_pi_binary()
    assert pi_client.get_pi_version(minimal_env=True) == "vtest"


def test_load_pi_catalog_parses_provider_requirements(monkeypatch, tmp_path: Path) -> None:
    payload = json.dumps(
        [
            {
                "id": "azure-openai-responses",
                "models": ["gpt-5.4-mini"],
                "availableModels": ["gpt-5.4-mini"],
                "defaultModel": "gpt-5.4-mini",
                "oauth": False,
                "oauthName": None,
                "requiresBaseUrl": True,
            },
            {
                "id": "openai",
                "models": ["gpt-5.4"],
                "availableModels": [],
                "defaultModel": "gpt-5.4",
                "oauth": False,
                "oauthName": None,
                "requiresBaseUrl": False,
            },
        ]
    )
    monkeypatch.setattr(pi_client, "PI_PACKAGE_ROOT", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        pi_client,
        "_run_pi_node_script",
        lambda script: SimpleNamespace(returncode=0, stdout=payload, stderr=""),
    )

    entries = pi_client._load_pi_catalog()

    assert entries[0].id == "azure-openai-responses"
    assert entries[0].available_models == ("gpt-5.4-mini",)
    assert entries[0].requires_base_url is True
    assert entries[1].id == "openai"
    assert entries[1].requires_base_url is False


def test_resolve_pi_model_uses_pi_provider_default_when_no_explicit_model(monkeypatch) -> None:
    monkeypatch.setattr(
        pi_client,
        "_get_active_provider_spec",
        lambda: SimpleNamespace(id="kimi-coding"),
    )
    monkeypatch.setattr(pi_client, "get_default_model", lambda: None)
    monkeypatch.setattr(
        pi_client,
        "_load_pi_provider_default_model",
        lambda provider_name: "kimi-k2-thinking",
    )

    assert pi_client.resolve_pi_model() == "kimi-k2-thinking"


def test_resolve_pi_model_allows_explicit_provider_model_not_yet_in_pi_catalog(
    monkeypatch,
) -> None:
    monkeypatch.setattr(pi_client, "_get_active_provider_spec", lambda: SimpleNamespace(id="zai"))
    monkeypatch.setattr(pi_client, "get_default_model", lambda: "glm-5.1")
    monkeypatch.setattr(
        pi_client,
        "_load_pi_provider_model_ids",
        lambda provider_name: ("glm-5", "glm-5-turbo"),
    )

    assert pi_client.resolve_pi_model() == "glm-5.1"


def test_build_subprocess_env_only_keeps_bounded_host_vars(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/tmp/home")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaked")
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "/tmp/pi-agent")

    env = pi_client._build_subprocess_env({"AZURE_OPENAI_API_KEY": "runtime-key"})

    assert env["HOME"] == "/tmp/home"
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["AZURE_OPENAI_API_KEY"] == "runtime-key"
    assert env["OPENAI_API_KEY"] == "host-openai"
    assert env["ANTHROPIC_API_KEY"] == "leaked"
    assert env["PI_CODING_AGENT_DIR"] == "/tmp/pi-agent"
    assert "CLAUDECODE" not in env


def test_probe_connection_uses_same_bounded_env_as_runtime(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("OPENAI_API_KEY", "host-openai")
    monkeypatch.setenv("UNSAFE_SECRET", "should-not-leak")
    monkeypatch.setattr(pi_client, "PI_LOCAL_PREFIX", tmp_path)
    monkeypatch.setattr(pi_client, "resolve_pi_binary", lambda: "/tmp/pi")

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, "ping", "")

    monkeypatch.setattr(pi_client.subprocess, "run", fake_run)

    ok, detail = pi_client.probe_pi_provider_connection("openai", "gpt-5.4")

    assert ok is True
    assert detail == "ping"
    assert seen["cmd"] == [
        "/tmp/pi",
        "--provider",
        "openai",
        "--model",
        "gpt-5.4",
        "--no-tools",
        "-p",
        "Reply with only: ping",
    ]
    assert seen["env"]["OPENAI_API_KEY"] == "host-openai"
    assert seen["env"]["PI_CODING_AGENT_DIR"] == str((tmp_path / "pi-agent").resolve())
    assert "UNSAFE_SECRET" not in seen["env"]


def test_probe_connection_returns_clean_timeout_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pi_client, "PI_LOCAL_PREFIX", tmp_path)
    monkeypatch.setattr(pi_client, "resolve_pi_binary", lambda: "/tmp/pi")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(pi_client.subprocess, "run", fake_run)

    ok, detail = pi_client.probe_pi_provider_connection(
        "kimi-coding",
        "k2p5",
        timeout_seconds=45,
    )

    assert ok is False
    assert detail == "probe timed out after 45s"


def test_runtime_start_passes_provider_and_exact_model_to_pi(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        return _FakeProcess()

    monkeypatch.setattr(pi_client, "resolve_pi_binary", lambda: "/tmp/pi")
    monkeypatch.setattr(
        pi_client,
        "resolve_pi_launch_binding",
        lambda model_override=None: pi_client.PiLaunchBinding(provider="zai", model="glm-5"),
    )
    monkeypatch.setattr(
        pi_client,
        "configure_pi_workspace",
        lambda *args, **kwargs: {"PI_CODING_AGENT_DIR": "/tmp/pi-agent"},
    )
    monkeypatch.setattr(pi_client.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pi_client.time, "sleep", lambda _seconds: None)

    runtime.start()

    assert captured["cmd"] == [
        "/tmp/pi",
        "--mode",
        "rpc",
        "--provider",
        "zai",
        "--model",
        "glm-5",
        "--session-dir",
        str(tmp_path / "sessions"),
    ]
    assert captured["env"]["PI_CODING_AGENT_DIR"] == "/tmp/pi-agent"
    assert runtime.status()["provider"] == "zai"


def test_new_session_uses_rpc_request(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    seen: dict[str, object] = {}

    def fake_send_request(
        command: dict[str, object], *, timeout: float = 30.0
    ) -> dict[str, object]:
        seen["command"] = command
        seen["timeout"] = timeout
        return {"cancelled": False}

    monkeypatch.setattr(runtime, "_send_request", fake_send_request)

    response = runtime.new_session(timeout=12.5)

    assert response == {"cancelled": False}
    assert seen == {"command": {"type": "new_session"}, "timeout": 12.5}


def test_prompt_falls_back_to_stream_tool_invocations_when_session_messages_omit_tool_calls(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)

    class _FakeStream:
        def __init__(self) -> None:
            self.events = [
                {
                    "type": "tool_execution_start",
                    "toolExecution": {
                        "id": "call_1",
                        "name": "bash",
                        "input": {"command": "pwd"},
                    },
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
                "stop_reason": "stop",
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return [{"name": "bash", "input": {"command": "pwd"}, "id": "call_1"}]

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 1})
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    )

    result = runtime.prompt("What happened?", timeout=5)

    assert result.tool_calls == [{"name": "bash", "input": {"command": "pwd"}, "id": "call_1"}]
    assert result.num_turns == 1


def test_prompt_falls_back_to_transcript_turns_when_session_stats_report_zero(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)

    class _FakeStream:
        def __init__(self) -> None:
            self.events = []
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
                "stop_reason": "stop",
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return []

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 0})
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: [
            {"role": "assistant", "content": [{"type": "text", "text": "first"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "second"}]},
        ],
    )

    result = runtime.prompt("What happened?", timeout=5)

    assert result.num_turns == 2


def test_prompt_tolerates_missing_stop_reason_in_message_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)

    class _FakeStream:
        def __init__(self) -> None:
            self.events = []
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
            return []

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 1})
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    )

    result = runtime.prompt("What happened?", timeout=5)

    assert result.status == "completed"
    assert result.response_id == "resp_123"
    assert result.stop_reason is None


def test_prompt_uses_retry_terminal_error_from_stream(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)

    class _FakeStream:
        def __init__(self) -> None:
            self.events = [
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
                },
                {
                    "type": "auto_retry_end",
                    "success": False,
                    "attempt": 3,
                    "finalError": '429 {"error":{"type":"rate_limit_error","message":"busy"}}',
                },
            ]
            self.error = None

        def set_callback(self, callback) -> None:
            self.callback = callback

        def reset(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> bool:  # pragma: no cover - assertion aid
            raise AssertionError("prompt should use wait_for_terminal_state when available")

        def wait_for_terminal_state(self, timeout: float | None = None) -> bool:
            return True

        def latest_retry_terminal_error(self) -> str | None:
            return '429 {"error":{"type":"rate_limit_error","message":"busy"}}'

        def get_output(self) -> str:
            return ""

        def get_thinking_chunks(self) -> list[str]:
            return []

        def get_usage(self) -> dict[str, int | float | None]:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }

        def get_message_metadata(self) -> dict[str, str | None]:
            return {
                "provider": "kimi-coding",
                "model": "k2p5",
                "response_id": "resp_retryable",
                "stop_reason": "error",
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return []

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 1})
    monkeypatch.setattr(runtime, "get_messages", lambda timeout=10.0: [])

    result = runtime.prompt("What happened?", timeout=5)

    assert result.status == "error"
    assert result.error == '429 {"error":{"type":"rate_limit_error","message":"busy"}}'


def test_prompt_serializes_concurrent_calls_on_shared_runtime(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(poll=lambda: None)

    class _FakeStream:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []
            self.error = None
            self._active = 0
            self._overlap = False
            self._lock = threading.Lock()

        def set_callback(self, callback) -> None:
            self.callback = callback

        def reset(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> bool:
            with self._lock:
                self._active += 1
                if self._active > 1:
                    self._overlap = True
            time.sleep(0.05)
            with self._lock:
                self._active -= 1
            return True

        def get_output(self) -> str:
            return "done"

        def get_thinking_chunks(self) -> list[str]:
            return []

        def get_usage(self) -> dict[str, int | float | None]:
            return {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }

        def get_message_metadata(self) -> dict[str, str | None]:
            return {
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "response_id": "resp_123",
                "stop_reason": "stop",
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return []

    stream = _FakeStream()
    runtime._stream = stream

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "new_session", lambda timeout=30.0: {})
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 1})
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    )

    results: list[pi_client.PiCycleResult] = []

    def _run_prompt() -> None:
        results.append(runtime.prompt("What happened?", timeout=5, new_session=True))

    first = threading.Thread(target=_run_prompt)
    second = threading.Thread(target=_run_prompt)
    first.start()
    second.start()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(results) == 2
    assert stream._overlap is False


def test_stop_waits_for_inflight_prompt_before_clearing_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(
        poll=lambda: None,
        pid=1234,
        wait=lambda timeout=5: None,
        terminate=lambda: None,
        kill=lambda: None,
    )

    class _FakeStream:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []
            self.error = None
            self.entered = threading.Event()
            self.release = threading.Event()

        def set_callback(self, callback) -> None:
            self.callback = callback

        def reset(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> bool:
            self.entered.set()
            assert self.release.wait(timeout or 1.0)
            return True

        def get_output(self) -> str:
            return "done"

        def get_thinking_chunks(self) -> list[str]:
            return []

        def get_usage(self) -> dict[str, int | float | None]:
            return {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }

        def get_message_metadata(self) -> dict[str, str | None]:
            return {
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "response_id": "resp_123",
                "stop_reason": "stop",
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return []

    stream = _FakeStream()
    runtime._stream = stream

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(runtime, "get_session_stats", lambda timeout=10.0: {"assistantMessages": 1})
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: [{"role": "assistant", "content": [{"type": "text", "text": "done"}]}],
    )

    prompt_errors: list[BaseException] = []
    stop_errors: list[BaseException] = []

    def _run_prompt() -> None:
        try:
            runtime.prompt("What happened?", timeout=1)
        except BaseException as exc:  # pragma: no cover - assertion aid
            prompt_errors.append(exc)

    def _run_stop() -> None:
        try:
            runtime.stop()
        except BaseException as exc:  # pragma: no cover - assertion aid
            stop_errors.append(exc)

    prompt_thread = threading.Thread(target=_run_prompt)
    prompt_thread.start()
    assert stream.entered.wait(0.2)

    stop_thread = threading.Thread(target=_run_stop)
    stop_thread.start()

    time.sleep(0.05)
    assert stop_thread.is_alive()
    assert runtime._process is not None

    stream.release.set()

    prompt_thread.join(timeout=1)
    stop_thread.join(timeout=1)

    assert not prompt_thread.is_alive()
    assert not stop_thread.is_alive()
    assert prompt_errors == []
    assert stop_errors == []
    assert runtime._process is None
    assert runtime._stream is None


def test_prompt_timeout_returns_timeout_and_restarts_runtime(tmp_path: Path, monkeypatch) -> None:
    runtime = _make_runtime(tmp_path, monkeypatch)
    runtime._process = SimpleNamespace(
        poll=lambda: None,
        pid=4321,
        wait=lambda timeout=5: None,
        terminate=lambda: None,
        kill=lambda: None,
    )

    class _FakeStream:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = [{"type": "text", "content": "partial"}]
            self.error = None

        def set_callback(self, callback) -> None:
            self.callback = callback

        def reset(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> bool:
            return False

        def get_output(self) -> str:
            return "partial"

        def get_thinking_chunks(self) -> list[str]:
            return ["thinking"]

        def get_usage(self) -> dict[str, int | float | None]:
            return {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": 0.0,
            }

        def get_message_metadata(self) -> dict[str, str | None]:
            return {
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "response_id": "resp_timeout",
                "stop_reason": None,
            }

        def get_assistant_error(self) -> str | None:
            return None

        def get_tool_invocations(self) -> list[dict[str, object]]:
            return []

    runtime._stream = _FakeStream()

    monkeypatch.setattr(runtime, "_send", lambda payload: None)
    monkeypatch.setattr(
        runtime,
        "get_session_stats",
        lambda timeout=10.0: (_ for _ in ()).throw(
            AssertionError("should not fetch stats after timeout")
        ),
    )
    monkeypatch.setattr(
        runtime,
        "get_messages",
        lambda timeout=10.0: (_ for _ in ()).throw(
            AssertionError("should not fetch messages after timeout")
        ),
    )

    result = runtime.prompt("What happened?", timeout=0.01)

    assert result.status == "timeout"
    assert result.error == "Pi did not complete within 0.01s"
    assert result.output == "partial"
    assert runtime._process is None
    assert runtime._stream is None
