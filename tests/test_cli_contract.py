from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import syke
from syke.entrypoint import cli
from syke.llm.pi_client import PiProviderCatalogEntry


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


def test_runtime_version_matches_project_metadata() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project_version = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"][
        "version"
    ]

    assert syke.__version__ == project_version


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


def test_setup_agent_needs_provider_returns_auth_exit_code(cli_runner) -> None:
    payload = {
        "provider": {"configured": False},
        "sources": [],
    }

    with (
        patch("syke.cli_commands.setup.build_setup_inspect_payload", return_value=payload),
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="/tmp/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.0.0"),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--agent"])

    assert result.exit_code == 3
    parsed = json.loads(result.output)
    assert parsed["status"] == "needs_provider"
    assert parsed["exit_code"] == 3


def test_status_json_returns_structured_payload(cli_runner) -> None:
    payload = {
        "ok": True,
        "user": "test",
        "provider": {"id": "openai", "configured": True},
        "daemon": {"running": False, "registered": False},
        "daemon_runtime": {"reachable": False, "alive": False, "detail": "socket missing"},
        "initialized": True,
        "cycle_count": 12,
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


def test_status_shows_daemon_warm_runtime_when_it_differs_from_config(cli_runner) -> None:
    payload = {
        "ok": True,
        "user": "test",
        "provider": {
            "id": "anthropic",
            "configured": True,
            "source": "Pi settings",
            "auth_source": "/tmp/auth.json",
            "model": "claude-sonnet-4-6",
            "model_source": "Pi settings defaultModel",
            "endpoint": "provider default",
            "endpoint_source": "Pi built-in/default",
        },
        "daemon": {"running": True, "registered": True},
        "daemon_runtime": {
            "ok": True,
            "reachable": True,
            "alive": True,
            "provider": "kimi-coding",
            "model": "k2p5",
            "runtime_pid": 777,
            "daemon_pid": 888,
            "detail": "kimi-coding / k2p5",
        },
        "initialized": False,
        "cycle_count": 0,
        "memex": {"present": False, "created_at": None, "memory_count": 0},
        "runtime_signals": {"daemon_ipc": {"ok": True, "detail": "socket present"}},
        "trust": {"sources": [], "targets": []},
    }

    with (
        patch("syke.cli_commands.status.get_db", return_value=MagicMock()),
        patch("syke.cli_commands.status.build_status_payload", return_value=payload),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "status"])

    assert result.exit_code == 0
    assert "provider: anthropic" in result.output
    assert "daemon warm runtime: kimi-coding / k2p5" in result.output
    assert "routing note: daemon runtime differs from current config" in result.output


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


def test_observe_text_renders_without_legacy_ingestion_payload(cli_runner) -> None:
    fake_db = MagicMock()
    payload = {
        "user_id": "test",
        "memory": {
            "active": 1,
            "retired": 0,
            "links": 0,
            "density": 0.0,
            "assessment": "healthy",
            "hubs": [],
            "supersession_max_depth": 0,
            "supersession_avg_depth": 0.0,
            "chains_with_history": 0,
            "orphan_count": 0,
            "orphan_pct": 0.0,
        },
        "synthesis": {
            "assessment": "never_run",
            "last_run_ago": "never",
            "created": 0,
            "superseded": 0,
            "linked": 0,
            "deactivated": 0,
            "duration_ms": 0,
            "cost_usd": 0.0,
            "memex_updated": False,
            "total_cost_usd": 0.0,
        },
        "runtime": {
            "recent_runs": 0,
            "last_run_ago": "never",
            "last_operation": None,
            "last_provider": None,
            "last_model": None,
            "avg_ask_ms": None,
            "avg_synthesis_ms": None,
            "total_tool_calls": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "warm_reuse_runs": 0,
            "cold_start_runs": 0,
            "daemon_ipc_runs": 0,
            "direct_runs": 0,
            "ipc_fallbacks": 0,
            "session_count": 0,
            "scripts_count": 0,
            "top_tools": [],
        },
        "memex": {
            "exists": False,
            "lines": 0,
            "chars": 0,
            "updated_ago": "never",
            "active_memories": 0,
        },
        "evolution": {
            "days": 7,
            "created": 0,
            "superseded": 0,
            "deactivated": 0,
            "net": 0,
            "links_per_day": 0.0,
            "supersession_rate": 0.0,
            "assessment": "dormant",
        },
        "signals": [],
    }

    with (
        patch("syke.cli_commands.status.get_db", return_value=fake_db),
        patch("syke.health.full_observe", return_value=payload),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "observe"])

    assert result.exit_code == 0
    assert "Syke — test" in result.output
    fake_db.close.assert_called_once()


def test_observe_days_option_threads_window_into_full_observe(cli_runner) -> None:
    fake_db = MagicMock()
    payload = {
        "user_id": "test",
        "memory": {
            "active": 0,
            "retired": 0,
            "links": 0,
            "density": 0.0,
            "assessment": "healthy",
            "hubs": [],
            "supersession_max_depth": 0,
            "supersession_avg_depth": 0.0,
            "chains_with_history": 0,
            "orphan_count": 0,
            "orphan_pct": 0.0,
        },
        "synthesis": {
            "assessment": "never_run",
            "last_run_ago": "never",
            "created": 0,
            "superseded": 0,
            "linked": 0,
            "deactivated": 0,
            "duration_ms": 0,
            "cost_usd": 0.0,
            "memex_updated": False,
            "total_cost_usd": 0.0,
        },
        "runtime": {
            "recent_runs": 0,
            "last_run_ago": "never",
            "last_operation": None,
            "last_provider": None,
            "last_model": None,
            "avg_ask_ms": None,
            "avg_synthesis_ms": None,
            "total_tool_calls": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "warm_reuse_runs": 0,
            "cold_start_runs": 0,
            "daemon_ipc_runs": 0,
            "direct_runs": 0,
            "ipc_fallbacks": 0,
            "session_count": 0,
            "scripts_count": 0,
            "top_tools": [],
        },
        "memex": {
            "exists": False,
            "lines": 0,
            "chars": 0,
            "updated_ago": "never",
            "active_memories": 0,
        },
        "evolution": {
            "days": 30,
            "created": 0,
            "superseded": 0,
            "deactivated": 0,
            "net": 0,
            "links_per_day": 0.0,
            "supersession_rate": 0.0,
            "assessment": "dormant",
        },
        "signals": [],
    }
    observe_fn = MagicMock(return_value=payload)

    with (
        patch("syke.cli_commands.status.get_db", return_value=fake_db),
        patch("syke.health.full_observe", observe_fn),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "observe", "--json", "--days", "30"])

    assert result.exit_code == 0
    observe_fn.assert_called_once_with(fake_db, "test", days=30)
    fake_db.close.assert_called_once()


def test_daemon_status_json_returns_structured_payload(cli_runner) -> None:
    metrics = MagicMock()
    metrics.get_summary.return_value = {
        "last_cycle": None,
        "last_run": {
            "completed_at": "2026-04-02T00:02:00+00:00",
            "success": True,
        },
    }

    with (
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 321, "source": "pidfile"},
        ),
        patch(
            "syke.daemon.daemon.launchd_metadata",
            return_value={"registered": True, "stale": False, "last_exit_status": 0},
        ),
        patch("syke.metrics.MetricsTracker", return_value=metrics),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=SimpleNamespace()),
        patch("syke.runtime.locator.describe_runtime_target", return_value="runtime-target"),
        patch(
            "syke.runtime.locator.resolve_background_syke_runtime", return_value=SimpleNamespace()
        ),
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={"reachable": False, "alive": False, "detail": "socket missing"},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["ok"] is True
    assert parsed["running"] is True
    assert parsed["pid"] == 321
    assert parsed["launcher_target"] == "runtime-target"


def test_daemon_status_json_includes_warm_runtime(cli_runner) -> None:
    metrics = MagicMock()
    metrics.get_summary.return_value = {"last_run": None, "last_cycle": None}

    with (
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 321, "source": "pidfile"},
        ),
        patch("syke.daemon.daemon.launchd_metadata", return_value={"registered": True}),
        patch("syke.metrics.MetricsTracker", return_value=metrics),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=SimpleNamespace()),
        patch("syke.runtime.locator.describe_runtime_target", return_value="runtime-target"),
        patch(
            "syke.runtime.locator.resolve_background_syke_runtime", return_value=SimpleNamespace()
        ),
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={
                "ok": True,
                "reachable": True,
                "alive": True,
                "provider": "kimi-coding",
                "model": "k2p5",
                "runtime_pid": 777,
                "daemon_pid": 321,
                "detail": "kimi-coding / k2p5",
            },
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["warm_runtime"]["provider"] == "kimi-coding"
    assert parsed["warm_runtime"]["model"] == "k2p5"


def test_daemon_status_json_prefers_last_cycle_truth_over_last_run(cli_runner) -> None:
    metrics = MagicMock()
    metrics.get_summary.return_value = {
        "last_run": {
            "completed_at": "2026-04-03T04:00:45+00:00",
            "success": True,
        },
        "last_cycle": {
            "operation": "synthesis_cycle",
            "status": "failed",
            "completed_at": "2026-04-03T04:00:45+00:00",
            "cost_usd": 0.0,
            "success": False,
        },
    }

    with (
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 321, "source": "launchd"},
        ),
        patch("syke.daemon.daemon.launchd_metadata", return_value={"registered": True}),
        patch("syke.metrics.MetricsTracker", return_value=metrics),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=SimpleNamespace()),
        patch("syke.runtime.locator.describe_runtime_target", return_value="runtime-target"),
        patch(
            "syke.runtime.locator.resolve_background_syke_runtime", return_value=SimpleNamespace()
        ),
        patch(
            "syke.daemon.ipc.daemon_runtime_status",
            return_value={"reachable": False, "alive": False, "detail": "socket missing"},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["last_run"]["success"] is False
    assert parsed["last_run"]["status"] == "failed"


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


def test_config_show_reports_only_live_truthful_knobs(cli_runner, monkeypatch) -> None:
    monkeypatch.setattr("syke.config.SYNC_THINKING_LEVEL", "medium")
    monkeypatch.setattr("syke.config.SYNC_TIMEOUT", 600)
    monkeypatch.setattr("syke.config.FIRST_RUN_SYNC_TIMEOUT", 1500)
    monkeypatch.setattr("syke.config.ASK_TIMEOUT", 300)
    monkeypatch.setattr("syke.config.DAEMON_INTERVAL", 900)
    monkeypatch.setattr("syke.config.DEFAULT_USER", "test")
    monkeypatch.setattr("syke.config.SYKE_HOME", Path("/tmp/syke-data"))
    monkeypatch.setattr(
        "syke.cli_commands.config._resolve_provider_display",
        lambda: (None, "", {}),
    )
    monkeypatch.setattr("syke.time.resolve_user_tz", lambda: "America/Los_Angeles")

    result = cli_runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert "thinking level: medium" in result.output
    assert "first run timeout: 1500s" in result.output
    assert "max_turns" not in result.output
    assert "25 turns" not in result.output
    assert "8192 tokens" not in result.output


def test_auth_status_reports_missing_auth_for_catalog_only_provider(
    cli_runner, monkeypatch
) -> None:
    payload = {
        "configured": False,
        "id": "anthropic",
        "source": "Pi settings",
        "runtime_provider": "anthropic",
        "auth_source": "catalog only (not daemon-safe)",
        "auth_configured": False,
        "model": "claude-sonnet-4-6",
        "model_source": "Pi settings defaultModel",
        "endpoint": "provider default",
        "endpoint_source": "Pi built-in/default",
        "error": "Run `syke auth login anthropic` or use Pi's `/login` flow.",
    }

    monkeypatch.setattr("syke.cli_commands.auth.run_setup_stage", lambda _label, fn: fn())
    monkeypatch.setattr("syke.cli_commands.auth.provider_payload", lambda _provider: payload)
    monkeypatch.setattr(
        "syke.cli_commands.auth.describe_provider", lambda *_args, **_kwargs: payload
    )
    monkeypatch.setattr("syke.pi_state.get_default_provider", lambda: "anthropic")
    monkeypatch.setattr("syke.pi_state.list_credential_providers", lambda: [])
    monkeypatch.setattr("syke.pi_state.load_pi_models", lambda: {})
    monkeypatch.setattr(
        "syke.llm.pi_client.get_pi_provider_catalog",
        lambda: [
            PiProviderCatalogEntry(
                "anthropic",
                ("claude-sonnet-4-6",),
                ("claude-sonnet-4-6",),
                "claude-sonnet-4-6",
                True,
                "Anthropic (Claude Pro/Max)",
            )
        ],
    )

    result = cli_runner.invoke(cli, ["auth", "status", "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["selected_provider"]["auth_source"] == "catalog only (not daemon-safe)"
    assert parsed["selected_provider"]["configured"] is False


def test_record_creates_memory(cli_runner) -> None:
    fake_db = MagicMock()
    fake_db.insert_memory.return_value = "mem-12345678"

    with patch("syke.cli_commands.record.get_db", return_value=fake_db):
        result = cli_runner.invoke(cli, ["--user", "test", "record", "hello world"])

    assert result.exit_code == 0
    fake_db.insert_memory.assert_called_once()
    mem = fake_db.insert_memory.call_args[0][0]
    assert mem.content == "hello world"
    assert mem.user_id == "test"


def test_config_pi_state_audit_prints_recent_lines(cli_runner, monkeypatch, tmp_path: Path) -> None:
    audit_path = tmp_path / "pi-state-audit.log"
    audit_path.write_text(
        '{"event":"set_default_provider","after":{"defaultProvider":"openai-codex"}}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(audit_path))

    result = cli_runner.invoke(cli, ["config", "pi-state-audit", "-n", "5"])

    assert result.exit_code == 0
    assert "set_default_provider" in result.output
    assert "openai-codex" in result.output
