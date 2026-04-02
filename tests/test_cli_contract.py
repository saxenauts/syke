from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from syke.cli import cli


def test_help_groups_primary_and_advanced_commands(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Primary Commands" in result.output
    assert "Advanced Commands" in result.output
    assert "setup" in result.output
    assert "ask" in result.output
    assert "record" in result.output
    assert "daemon" in result.output
    assert "self-update" in result.output
    assert "install-current" in result.output
    assert "ingest" not in result.output


def test_setup_json_is_inspect_only(cli_runner) -> None:
    payload = {
        "ok": True,
        "schema_version": 1,
        "mode": "inspect",
        "user": "test",
        "provider": {"configured": False},
        "provider_choices": [{"id": "openai"}],
        "sources": [],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "runtime": {"ready": False},
        "daemon": {"platform": "Darwin", "installable": True, "running": False},
        "proposed_actions": [],
        "consent_points": [],
        "next_commands": ["syke status --json"],
    }

    with patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["mode"] == "inspect"
    assert parsed["user"] == "test"
    assert "daemon" in parsed
    assert "runtime" in parsed


def test_status_json_returns_structured_payload(cli_runner) -> None:
    payload = {
        "ok": True,
        "user": "test",
        "provider": {"id": "openai", "configured": True},
        "daemon": {"running": False, "registered": False},
        "sources": {"codex": 12},
        "total_events": 12,
        "latest_event_at": "2026-04-02T00:00:00+00:00",
        "recent_runs": [],
        "memex": {"present": True, "created_at": "2026-04-02T00:01:00+00:00", "memory_count": 2},
        "runtime_signals": {"daemon_ipc": {"ok": False, "detail": "socket missing"}},
        "trust": {"sources": [], "targets": []},
    }

    with (
        patch("syke.cli_commands.status.get_db", return_value=MagicMock()),
        patch("syke.cli_commands.status.build_status_payload", return_value=payload),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["user"] == "test"
    assert parsed["provider"]["id"] == "openai"
    assert parsed["daemon"]["running"] is False


def test_doctor_json_returns_structured_payload(cli_runner) -> None:
    payload = {
        "ok": False,
        "user": "test",
        "checks": {
            "provider": {"label": "Provider", "ok": False, "detail": "missing"},
            "daemon": {"label": "Daemon", "ok": False, "detail": "not running"},
        },
        "events": None,
        "memory_health": None,
        "network": None,
    }

    with patch("syke.cli_commands.status.build_doctor_payload", return_value=payload):
        result = cli_runner.invoke(cli, ["--user", "test", "doctor", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["user"] == "test"
    assert parsed["checks"]["provider"]["detail"] == "missing"


def test_context_format_json_returns_machine_payload(cli_runner) -> None:
    fake_db = MagicMock()

    with (
        patch("syke.cli_commands.status.get_db", return_value=fake_db),
        patch(
            "syke.memory.memex.get_memex_for_injection",
            return_value="# Memex\n- current focus",
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "context", "--format", "json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed == {"memex": "# Memex\n- current focus", "user": "test"}
    fake_db.close.assert_called_once()


def test_ask_json_returns_structured_result(cli_runner) -> None:
    fake_db = MagicMock()

    with (
        patch("syke.cli.get_db", return_value=fake_db),
        patch("syke.cli_commands.ask.get_db", return_value=fake_db),
        patch(
            "syke.llm.env.resolve_provider",
            return_value=SimpleNamespace(id="openai"),
        ),
        patch(
            "syke.llm.pi_runtime.run_ask",
            return_value=(
                "final answer",
                {
                    "provider": "openai",
                    "duration_ms": 123,
                    "cost_usd": 0.01,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "tool_calls": 1,
                },
            ),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "what changed?", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["question"] == "what changed?"
    assert parsed["answer"] == "final answer"
    assert parsed["provider"] == "openai"
    fake_db.close.assert_called_once()


def test_ask_jsonl_streams_status_events_and_result(cli_runner) -> None:
    fake_db = MagicMock()

    def fake_run_ask(*, db, user_id, question, on_event):
        del db, user_id, question
        on_event(SimpleNamespace(type="thinking", content="considering", metadata=None))
        on_event(
            SimpleNamespace(
                type="tool_call",
                content="search",
                metadata={"input": {"query": "recent work"}},
            )
        )
        on_event(SimpleNamespace(type="text", content="answer text", metadata=None))
        return "answer text", {
            "provider": "openai",
            "duration_ms": 50,
            "cost_usd": 0.0,
            "input_tokens": 5,
            "output_tokens": 7,
            "tool_calls": 1,
        }

    with (
        patch("syke.cli.get_db", return_value=fake_db),
        patch("syke.cli_commands.ask.get_db", return_value=fake_db),
        patch(
            "syke.llm.env.resolve_provider",
            return_value=SimpleNamespace(id="openai"),
        ),
        patch("syke.llm.pi_runtime.run_ask", side_effect=fake_run_ask),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "what changed?", "--jsonl"])

    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.output.strip().splitlines()]
    assert lines[0] == {"type": "status", "phase": "starting", "provider": "openai"}
    assert any(line["type"] == "thinking" for line in lines)
    assert any(line["type"] == "tool_call" for line in lines)
    assert any(line["type"] == "text" for line in lines)
    result_line = next(line for line in lines if line["type"] == "result")
    assert result_line["ok"] is True
    assert result_line["answer"] == "answer text"
    fake_db.close.assert_called_once()
