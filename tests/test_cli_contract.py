from __future__ import annotations

import json
from pathlib import Path
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


def test_ingest_command_is_not_registered() -> None:
    assert cli.get_command(None, "ingest") is None


def test_auth_command_routes_to_extracted_group() -> None:
    auth_cmd = cli.get_command(None, "auth")

    assert auth_cmd is not None
    assert auth_cmd.callback.__module__ == "syke.cli_commands.auth"

    for subcommand in ("status", "set", "login", "use", "unset"):
        nested = auth_cmd.get_command(None, subcommand)
        assert nested is not None
        assert nested.callback.__module__ == "syke.cli_commands.auth"


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


def test_setup_noninteractive_without_provider_returns_auth_exit(cli_runner) -> None:
    payload = {
        "provider": {"configured": False},
        "sources": [],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.cli_commands.setup.render_setup_inspect_summary"),
        patch("syke.cli_commands.setup.run_setup_stage", side_effect=lambda _label, fn: fn()),
        patch("syke.cli_commands.setup.ensure_setup_pi_runtime", return_value=("pi", "1.0.0")),
        patch("syke.cli_commands.setup.sys.stdin.isatty", return_value=False),
        patch("syke.cli_commands.setup.run_interactive_provider_flow") as provider_flow,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes"])

    assert result.exit_code == 3
    assert "Setup requires a configured provider." in result.output
    provider_flow.assert_not_called()


def test_setup_runtime_failure_uses_runtime_exit_code(cli_runner) -> None:
    from syke.cli_support.exit_codes import SykeRuntimeException

    payload = {
        "provider": {"configured": True, "id": "openrouter"},
        "sources": [],
        "trust": {"sources": [], "targets": []},
        "setup_targets": [],
        "daemon": {"platform": "Darwin", "installable": False, "running": False},
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.cli_commands.setup.render_setup_inspect_summary"),
        patch("syke.cli_commands.setup.run_setup_stage", side_effect=lambda _label, fn: fn()),
        patch(
            "syke.cli_commands.setup.ensure_setup_pi_runtime",
            side_effect=SykeRuntimeException("Pi runtime unavailable."),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes"])

    assert result.exit_code == 4
    assert "Pi runtime unavailable." in result.output


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


def test_context_json_flag_returns_machine_payload(cli_runner) -> None:
    fake_db = MagicMock()

    with (
        patch("syke.cli_commands.status.get_db", return_value=fake_db),
        patch(
            "syke.memory.memex.get_memex_for_injection",
            return_value="# Memex\n- current focus",
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "context", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed == {"memex": "# Memex\n- current focus", "user": "test"}
    fake_db.close.assert_called_once()


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


def test_ask_json_missing_provider_returns_auth_exit_code(cli_runner) -> None:
    fake_db = MagicMock()

    with (
        patch("syke.cli.get_db", return_value=fake_db),
        patch("syke.cli_commands.ask.get_db", return_value=fake_db),
        patch(
            "syke.llm.env.resolve_provider",
            side_effect=RuntimeError("No provider configured. Run `syke setup`."),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "what changed?", "--json"])

    assert result.exit_code == 3
    parsed = json.loads(result.output)
    assert parsed["ok"] is False
    assert "No provider configured" in parsed["error"]
    fake_db.close.assert_called_once()


def test_ask_json_invalid_provider_returns_usage_exit_code(cli_runner) -> None:
    fake_db = MagicMock()

    with (
        patch("syke.cli.get_db", return_value=fake_db),
        patch("syke.cli_commands.ask.get_db", return_value=fake_db),
        patch(
            "syke.llm.env.resolve_provider",
            side_effect=ValueError("Unknown provider 'bad'. Valid providers: openai"),
        ),
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "--provider", "bad", "ask", "what changed?", "--json"],
        )

    assert result.exit_code == 2
    parsed = json.loads(result.output)
    assert parsed["ok"] is False
    assert "Unknown provider" in parsed["error"]
    fake_db.close.assert_called_once()


def test_sync_json_returns_structured_payload(cli_runner) -> None:
    fake_db = MagicMock()
    fake_db.get_sources.return_value = ["codex", "claude-code"]

    with (
        patch("syke.cli_commands.maintenance.get_db", return_value=fake_db),
        patch("syke.sync.run_sync", return_value=(7, ["codex"])),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "sync", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["sources_count"] == 2
    assert parsed["synced_sources"] == ["codex"]
    assert parsed["total_new_events"] == 7
    fake_db.close.assert_called_once()


def test_observe_json_returns_structured_payload(cli_runner) -> None:
    fake_db = MagicMock()
    payload = {"ok": True, "summary": {"events": 4}}

    with (
        patch("syke.cli_commands.status.get_db", return_value=fake_db),
        patch("syke.health.full_observe", return_value=payload),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "observe", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["summary"]["events"] == 4
    fake_db.close.assert_called_once()


def test_observe_json_and_watch_are_mutually_exclusive(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["--user", "test", "observe", "--json", "--watch"])

    assert result.exit_code != 0
    assert "--json and --watch are mutually exclusive." in result.output


def test_daemon_status_json_returns_structured_payload(cli_runner) -> None:
    metrics = MagicMock()
    metrics.get_summary.return_value = {
        "last_run": {
            "completed_at": "2026-04-02T00:02:00+00:00",
            "events_processed": 12,
            "success": True,
        }
    }

    with (
        patch("syke.daemon.daemon.is_running", return_value=(True, 321)),
        patch(
            "syke.daemon.daemon.launchd_metadata",
            return_value={"registered": True, "stale": False, "last_exit_status": 0},
        ),
        patch("syke.daemon.metrics.MetricsTracker", return_value=metrics),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=SimpleNamespace()),
        patch("syke.runtime.locator.describe_runtime_target", return_value="runtime-target"),
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=SimpleNamespace()),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["running"] is True
    assert parsed["pid"] == 321
    assert parsed["last_run"]["events_processed"] == 12
    assert parsed["launcher_target"] == "runtime-target"


def test_daemon_logs_json_returns_line_payload(cli_runner, tmp_path: Path) -> None:
    log_path = tmp_path / "daemon.log"
    log_path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    with patch("syke.daemon.daemon.LOG_PATH", log_path):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "logs", "--json", "-n", "2"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["path"] == str(log_path)
    assert parsed["lines"] == ["two", "three"]
