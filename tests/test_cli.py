from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from syke.db import SykeDB
from syke.cli import cli
from syke.llm.backends import AskEvent
from syke.llm.pi_runtime import run_ask, run_ask_stream
from syke.models import Event
from syke.runtime.locator import SykeRuntimeDescriptor
from syke.config import PROJECT_ROOT


def _seed_events(db, user_id: str, count: int = 3) -> None:
    for i in range(count):
        db.insert_event(
            Event(
                user_id=user_id,
                source="test-source",
                event_type="conversation",
                title=f"Event {i}",
                content=f"Content {i}",
                timestamp=datetime(2026, 2, 10 + i, 12, 0, 0),
            )
        )


# --- Dashboard ---


@pytest.mark.parametrize("has_db", [False, True], ids=["without_db", "with_db"])
def test_dashboard_shows_status_when_invoked_without_subcommand(cli_runner, tmp_path, has_db):
    mock_db = MagicMock()
    mock_db.count_events.return_value = 42
    mock_db.get_status.return_value = {"latest_event_at": "2025-01-01T00:00:00"}
    mock_db.get_memex.return_value = {"content": "# Memex"}
    mock_db.count_memories.return_value = 5

    db_path = tmp_path / "syke.db"
    if has_db:
        db_path.touch()

    from syke.llm.providers import PROVIDERS

    with (
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["codex"]),
        patch(
            "syke.cli.user_syke_db_path",
            return_value=db_path if has_db else MagicMock(exists=lambda: False),
        ),
        patch("syke.cli.get_db", return_value=mock_db),
        patch("platform.system", return_value="Darwin"),
        patch(
            "syke.daemon.daemon.launchd_status",
            return_value='"LastExitStatus" = 0;' if has_db else None,
        ),
        patch("syke.distribution.harness.status_all", return_value=[]),
    ):
        result = cli_runner.invoke(cli, ["--user", "test"])

    assert result.exit_code == 0
    assert not result.output.strip().startswith("Usage:")
    assert "Syke" in result.output
    assert "Provider" in result.output
    assert "Daemon" in result.output
    if has_db:
        assert "42" in result.output
        assert "synthesized" in result.output
        assert "5 memories" in result.output
    else:
        assert "not initialized" in result.output


def test_top_level_help_groups_primary_and_advanced_commands(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Primary Commands:" in result.output
    assert "Advanced Commands:" in result.output
    for name in ("setup", "ask", "context", "record", "status", "sync", "auth", "doctor"):
        assert name in result.output
    for name in ("daemon", "config", "connect", "cost", "observe", "self-update"):
        assert name in result.output
    assert "sense" not in result.output
    assert "dev" not in result.output


def test_ingest_help_hides_legacy_chatgpt_import(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["ingest", "--help"])

    assert result.exit_code == 0
    assert "chatgpt" not in result.output


def test_legacy_chatgpt_import_command_is_disabled(cli_runner, tmp_path) -> None:
    export_zip = tmp_path / "chatgpt-export.zip"
    export_zip.touch()

    result = cli_runner.invoke(
        cli,
        ["--user", "test", "ingest", "chatgpt", "--file", str(export_zip), "--yes"],
    )

    assert result.exit_code != 0
    assert "deprecated and disabled" in result.output


# --- Context ---


@pytest.mark.parametrize(
    "fmt,memex,expected_text,is_json",
    [
        ("markdown", "", "No memex", False),
        ("markdown", "# My Memex\nHello world", "# My Memex", False),
        ("json", "hello", "", True),
    ],
    ids=["no_memex", "markdown", "json"],
)
def test_context_outputs_expected_format(cli_runner, fmt, memex, expected_text, is_json):
    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.memory.memex.get_memex_for_injection", return_value=memex),
    ):
        args = ["--user", "test", "context"]
        if fmt != "markdown":
            args.extend(["--format", fmt])
        result = cli_runner.invoke(cli, args)

    assert result.exit_code == 0
    if is_json:
        payload = json.loads(result.output)
        assert payload["memex"] == "hello"
        assert payload["user"] == "test"
    else:
        assert expected_text in result.output


# --- Status ---


def test_status_json_outputs_machine_readable_payload(cli_runner) -> None:
    mock_db = MagicMock()
    mock_db.get_status.return_value = {
        "sources": {"codex": 10, "chatgpt": 2},
        "total_events": 12,
        "latest_event_at": "2026-03-28T08:00:00+00:00",
        "recent_runs": [{"status": "completed", "source": "codex", "events_count": 3}],
    }
    mock_db.get_memex.return_value = {"created_at": "2026-03-28T08:05:00+00:00"}
    mock_db.count_memories.return_value = 7

    from syke.llm.providers import PROVIDERS
    fake_cfg = SimpleNamespace(providers={}, models=SimpleNamespace(synthesis="gpt-5.4-mini"))

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.config.CFG", fake_cfg),
        patch("syke.llm.AuthStore") as MockStore,
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["codex"]),
        patch(
            "syke.llm.codex_auth.read_codex_auth",
            return_value=SimpleNamespace(is_expired=False),
        ),
        patch("syke.llm.codex_auth.get_codex_model", return_value="gpt-5.4-codex"),
        patch("platform.system", return_value="Linux"),
        patch("syke.daemon.daemon.is_running", return_value=(True, 321)),
    ):
        store = MockStore.return_value
        store.get_token.return_value = None
        result = cli_runner.invoke(cli, ["--user", "test", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["user"] == "test"
    assert payload["provider"]["id"] == "codex"
    assert payload["provider"]["auth_source"] == "~/.codex/auth.json"
    assert payload["provider"]["model"] == "gpt-5.4-codex"
    assert payload["provider"]["endpoint"] == "provider default"
    assert payload["daemon"]["running"] is True
    assert payload["sources"] == {"codex": 10, "chatgpt": 2}
    assert payload["total_events"] == 12
    assert payload["memex"]["present"] is True
    assert payload["memex"]["memory_count"] == 7
    assert "trust" in payload
    assert "sources" in payload["trust"]
    assert "targets" in payload["trust"]


def test_status_human_output_shows_runtime_resolution(cli_runner) -> None:
    mock_db = MagicMock()
    mock_db.get_status.return_value = {
        "sources": {},
        "total_events": 0,
        "latest_event_at": None,
        "recent_runs": [],
    }
    mock_db.get_memex.return_value = None

    from syke.llm.providers import PROVIDERS
    fake_cfg = SimpleNamespace(
        providers={"openai": {"model": "gpt-5.4", "base_url": "https://proxy.example/v1"}},
        models=SimpleNamespace(synthesis="gpt-5-mini"),
    )

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.config.CFG", fake_cfg),
        patch("syke.llm.AuthStore") as MockStore,
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["openai"]),
        patch("platform.system", return_value="Linux"),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
    ):
        store = MockStore.return_value
        store.get_token.return_value = "sk-test"
        result = cli_runner.invoke(cli, ["--user", "test", "status"])

    assert result.exit_code == 0
    assert "Runtime" in result.output
    assert "auth: ~/.syke/auth.json" in result.output
    assert "model: gpt-5.4" in result.output
    assert "endpoint: https://proxy.example/v1" in result.output


# --- Doctor ---


@pytest.mark.parametrize(
    "has_binary,has_db,expected_failures",
    [
        (True, False, ["Syke DB", "Events DB", "Daemon"]),
        (False, False, ["Pi runtime", "Syke DB", "Events DB", "Daemon"]),
    ],
    ids=["mixed_checks", "all_failing"],
)
def test_doctor_reports_expected_failures(
    cli_runner, tmp_path, has_binary, has_db, expected_failures
):
    from syke.llm.providers import PROVIDERS

    pi_bin = tmp_path / "pi"
    if has_binary:
        pi_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    def _fake_pi_version(*, install: bool = False, minimal_env: bool = False, timeout: int = 10) -> str:
        del install, timeout
        if minimal_env:
            return "1.2.3"
        return "1.2.3"

    fake_runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
        package_version="0.4.6",
    )

    with (
        patch("shutil.which", return_value="/usr/bin/claude" if has_binary else None),
        patch("syke.cli.user_syke_db_path", return_value=MagicMock(exists=lambda: has_db)),
        patch("syke.cli.user_events_db_path", return_value=MagicMock(exists=lambda: has_db)),
        patch("syke.daemon.daemon.launchd_status", return_value=None),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.distribution.harness.status_all", return_value=[]),
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["codex"]),
        patch(
            "syke.llm.codex_auth.read_codex_auth",
            return_value=SimpleNamespace(is_expired=False),
        ),
        patch("syke.llm.pi_client.PI_BIN", pi_bin),
        patch("syke.llm.pi_client.get_pi_version", side_effect=_fake_pi_version),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=fake_runtime),
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=fake_runtime),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "doctor"])

    assert result.exit_code == 0
    assert result.output.count("FAIL") == len(expected_failures)
    for failure in expected_failures:
        assert f"FAIL  {failure}:" in result.output
    assert "Provider" in result.output
    assert "Pi runtime" in result.output
    assert "Launcher" in result.output
    assert "Syke DB" in result.output
    assert "Events DB" in result.output
    assert "Daemon" in result.output
    if has_binary:
        assert "Pi cold-start" in result.output
    else:
        assert "Pi cold-start" not in result.output


def test_doctor_json_outputs_machine_readable_payload(cli_runner, tmp_path) -> None:
    from syke.llm.providers import PROVIDERS

    pi_bin = tmp_path / "pi"
    pi_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    syke_db = tmp_path / "syke.db"
    events_db = tmp_path / "events.db"
    syke_db.touch()
    events_db.touch()

    fake_runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
        package_version="0.4.6",
    )
    mock_db = MagicMock()
    mock_db.count_events.return_value = 42

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.cli.user_syke_db_path", return_value=syke_db),
        patch("syke.cli.user_events_db_path", return_value=events_db),
        patch("syke.daemon.daemon.launchd_status", return_value=None),
        patch("syke.daemon.daemon.is_running", return_value=(True, 999)),
        patch("syke.distribution.harness.status_all", return_value=[]),
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["codex"]),
        patch(
            "syke.llm.codex_auth.read_codex_auth",
            return_value=SimpleNamespace(is_expired=False),
        ),
        patch("syke.llm.env.build_pi_runtime_env", return_value={"OPENAI_API_KEY": "sk-test"}),
        patch("syke.llm.pi_client.PI_BIN", pi_bin),
        patch("syke.llm.pi_client.get_pi_version", return_value="1.2.3"),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=fake_runtime),
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=fake_runtime),
        patch(
            "syke.health.memory_health",
            return_value={"assessment": "healthy", "active": 5, "links": 9, "orphan_pct": 0},
        ),
        patch(
            "syke.health.synthesis_health",
            return_value={"assessment": "recent", "last_run_ago": "2m ago"},
        ),
        patch(
            "syke.health.memex_health",
            return_value={"assessment": "fresh", "lines": 20, "updated_ago": "1m ago"},
        ),
        patch(
            "syke.health.evolution_trends",
            return_value={"assessment": "active", "days": 7, "created": 3, "superseded": 1},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["user"] == "test"
    assert payload["checks"]["provider"]["ok"] is True
    assert payload["checks"]["pi_runtime"]["ok"] is True
    assert payload["checks"]["daemon"]["ok"] is True
    assert payload["events"] == 42
    assert payload["memory_health"]["graph"]["assessment"] == "healthy"
    assert payload["harness_adapters"] == []


def test_dev_install_safe_helper(cli_runner, tmp_path):
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    completed = subprocess.CompletedProcess(["uv"], 0)

    with (
        patch("syke.cli._is_source_install", return_value=True),
        patch("syke.cli.PROJECT_ROOT", fake_root),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = cli_runner.invoke(cli, ["dev", "install-safe"])

    assert result.exit_code == 0
    run_mock.assert_called_once_with(
        [
            "uv",
            "tool",
            "install",
            "--force",
            "--reinstall",
            "--refresh",
            "--no-cache",
            ".",
        ],
        cwd=str(fake_root),
        check=False,
    )
    assert "Managed install refreshed" in result.output


def test_daemon_help_lists_canonical_subcommands(cli_runner) -> None:
    result = cli_runner.invoke(cli, ["daemon", "--help"])

    assert result.exit_code == 0
    assert "start" in result.output
    assert "stop" in result.output
    assert "status" in result.output
    assert "daemon-start" not in result.output
    assert "daemon-stop" not in result.output


def test_daemon_start_invokes_install(cli_runner) -> None:
    mock_install = MagicMock()

    with (
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.daemon.daemon.install_and_start", mock_install),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "start"])

    assert result.exit_code == 0
    mock_install.assert_called_once_with("test", 900)


def test_install_current_uses_uv_and_restarts_daemon(cli_runner) -> None:
    completed = subprocess.CompletedProcess(["uv"], 0)

    with (
        patch("syke.cli._is_source_install", return_value=True),
        patch("syke.cli._resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, 123)),
        patch("syke.daemon.daemon.stop_and_unload") as stop_mock,
        patch("syke.daemon.daemon.install_and_start") as start_mock,
        patch("subprocess.run", return_value=completed) as run_mock,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "install-current", "--yes"])

    assert result.exit_code == 0
    run_mock.assert_called_once_with(
        ["uv", "tool", "install", "--force", "--reinstall", "--refresh", "--no-cache", "."],
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    stop_mock.assert_called_once()
    start_mock.assert_called_once_with("test")
    assert "Managed install refreshed" in result.output


def test_install_current_requires_source_checkout(cli_runner) -> None:
    with patch("syke.cli._is_source_install", return_value=False):
        result = cli_runner.invoke(cli, ["install-current", "--yes"])

    assert result.exit_code != 0
    assert "only works from a source checkout" in result.output


# --- Record ---


def test_record_pushes_basic_text_event(cli_runner):
    mock_gateway = MagicMock()
    mock_gateway.push.return_value = {
        "status": "ok",
        "event_id": "abcd1234-5678",
        "duplicate": False,
    }

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.observe.importers.IngestGateway", return_value=mock_gateway),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "record", "Prefers dark mode"])

    assert result.exit_code == 0
    assert "Recorded" in result.output
    assert "abcd1234" in result.output
    assert mock_gateway.push.call_args.kwargs["source"] == "manual"
    assert mock_gateway.push.call_args.kwargs["content"] == "Prefers dark mode"


def test_record_includes_tags_and_custom_source(cli_runner):
    mock_gateway = MagicMock()
    mock_gateway.push.return_value = {
        "status": "ok",
        "event_id": "abcd1234-5678",
        "duplicate": False,
    }

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.observe.importers.IngestGateway", return_value=mock_gateway),
    ):
        result = cli_runner.invoke(
            cli,
            [
                "--user",
                "test",
                "record",
                "-t",
                "work",
                "-t",
                "pref",
                "--source",
                "cursor",
                "Observation",
            ],
        )

    assert result.exit_code == 0
    kwargs = mock_gateway.push.call_args.kwargs
    assert kwargs["source"] == "cursor"
    assert kwargs["metadata"] == {"tags": ["work", "pref"]}


def test_record_reads_from_stdin_when_no_text_argument(cli_runner):
    mock_gateway = MagicMock()
    mock_gateway.push.return_value = {
        "status": "ok",
        "event_id": "abcd1234-5678",
        "duplicate": False,
    }

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.observe.importers.IngestGateway", return_value=mock_gateway),
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "record"],
            input="Long research dump\nWith multiple lines",
        )

    assert result.exit_code == 0
    pushed_content = mock_gateway.push.call_args.kwargs["content"]
    assert "Long research dump" in pushed_content
    assert "multiple lines" in pushed_content


@pytest.mark.parametrize("scenario", ["duplicate", "empty"], ids=["duplicate", "empty_input"])
def test_record_handles_duplicate_and_empty_inputs(cli_runner, scenario):
    mock_gateway = MagicMock()
    mock_gateway.push.return_value = {
        "status": "duplicate",
        "event_id": "abc",
        "duplicate": True,
    }

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.observe.importers.IngestGateway", return_value=mock_gateway),
    ):
        if scenario == "duplicate":
            result = cli_runner.invoke(cli, ["--user", "test", "record", "Same event"])
            assert result.exit_code == 0
            assert "duplicate" in result.output.lower()
        else:
            result = cli_runner.invoke(cli, ["--user", "test", "record"])
            assert result.exit_code != 0
            assert "Nothing to record" in result.output


@pytest.mark.parametrize("mode", ["json", "jsonl"], ids=["single_json", "jsonl_batch"])
def test_record_supports_json_and_jsonl_input(cli_runner, mode):
    mock_gateway = MagicMock()
    mock_gateway.push.return_value = {
        "status": "ok",
        "event_id": "abcd1234-5678",
        "duplicate": False,
    }
    mock_gateway.push_batch.return_value = {
        "status": "ok",
        "inserted": 2,
        "duplicates": 0,
        "filtered": 0,
        "errors": [],
        "total": 2,
    }

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.observe.importers.IngestGateway", return_value=mock_gateway),
    ):
        if mode == "json":
            payload = json.dumps({"text": "JSON observation", "tags": ["test"]})
            result = cli_runner.invoke(
                cli,
                ["--user", "test", "record", "--json", payload],
            )
            assert result.exit_code == 0
            assert mock_gateway.push.call_args.kwargs["content"] == "JSON observation"
        else:
            lines = "\n".join(
                [
                    json.dumps(
                        {
                            "source": "test",
                            "event_type": "note",
                            "title": "A",
                            "content": "First",
                        }
                    ),
                    json.dumps(
                        {
                            "source": "test",
                            "event_type": "note",
                            "title": "B",
                            "content": "Second",
                        }
                    ),
                ]
            )
            result = cli_runner.invoke(
                cli,
                ["--user", "test", "record", "--jsonl"],
                input=lines,
            )
            assert result.exit_code == 0
            assert "Recorded" in result.output
            mock_gateway.push_batch.assert_called_once()

# --- Self-update ---


@pytest.mark.parametrize(
    "check_result,expected_text",
    [
        ((False, "0.2.9"), "Already up to date"),
        ((False, None), "Could not reach PyPI"),
    ],
    ids=["already_current", "network_failure"],
)
def test_self_update_handles_current_or_network_cases(cli_runner, check_result, expected_text):
    with (
        patch("syke.cli.__version__", "0.2.9"),
        patch("syke.version_check.check_update_available", return_value=check_result),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update"])

    assert result.exit_code == 0
    assert expected_text in result.output


@pytest.mark.parametrize(
    "install_method,expected_text",
    [("source", "git pull"), ("uvx", "uvx")],
    ids=["source_install", "uvx_install"],
)
def test_self_update_exits_early_for_source_and_uvx(cli_runner, install_method, expected_text):
    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli._detect_install_method", return_value=install_method),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert expected_text in result.output


@pytest.mark.parametrize(
    "install_method,expected_cmd",
    [
        ("pipx", ["pipx", "upgrade", "syke"]),
        ("pip", ["pip", "install", "--upgrade", "syke"]),
    ],
    ids=["pipx", "pip"],
)
def test_self_update_runs_upgrade_command_for_install_method(
    cli_runner, install_method, expected_cmd
):
    mock_run = MagicMock(return_value=MagicMock(returncode=0))

    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli._detect_install_method", return_value=install_method),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("subprocess.run", mock_run),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert any(call.args[0] == expected_cmd for call in mock_run.call_args_list)


def test_self_update_restarts_daemon_when_previously_running(cli_runner):
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    mock_stop = MagicMock()
    mock_start = MagicMock()

    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli._detect_install_method", return_value="pipx"),
        patch("syke.daemon.daemon.is_running", return_value=(True, 123)),
        patch("syke.daemon.daemon.stop_and_unload", mock_stop),
        patch("syke.daemon.daemon.install_and_start", mock_start),
        patch("subprocess.run", mock_run),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    mock_stop.assert_called_once()
    mock_start.assert_called_once()


# --- Ask ---


@pytest.mark.parametrize("mode", ["ask", "ask_stream"], ids=["non_stream", "stream"])
def test_ask_returns_no_data_message_without_events(db, user_id, mode):
    if mode == "ask":
        result, cost = run_ask(db, user_id, "What is the user working on?")
    else:
        events: list[AskEvent] = []
        result, cost = run_ask_stream(db, user_id, "What is the user working on?", events.append)

    assert "no data" in result.lower()
    assert cost == {
        "backend": "pi",
        "cost_usd": None,
        "duration_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "tool_calls": None,
        "num_turns": None,
        "provider": None,
        "model": None,
        "error": None,
    }


def test_ask_returns_answer_from_pi_backend(db, user_id):
    _seed_events(db, user_id, 5)

    with patch(
        "syke.llm.backends.pi_ask.pi_ask",
        return_value=(
            "They are building Syke for a hackathon.",
            {
                "backend": "pi",
                "cost_usd": 0.01,
                "duration_ms": 100,
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "tool_calls": 0,
                "num_turns": 1,
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "error": None,
            },
        ),
    ):
        result, _cost = run_ask(db, user_id, "What is the user working on?")

    assert "Syke" in result


def test_ask_errors_are_returned_in_metadata(db, user_id):
    _seed_events(db, user_id, 5)

    with patch(
        "syke.llm.backends.pi_ask.pi_ask",
        return_value=(
            "Pi ask failed: backend exploded",
            {
                "backend": "pi",
                "cost_usd": None,
                "duration_ms": 50,
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "tool_calls": 0,
                "num_turns": 0,
                "provider": None,
                "model": None,
                "error": "Pi ask failed: backend exploded",
            },
        ),
    ):
        result, cost = run_ask(db, user_id, "What is happening?")

    assert "failed" in result.lower()
    assert cost["backend"] == "pi"
    assert cost["error"] == "Pi ask failed: backend exploded"


def test_ask_stream_emits_pi_events(db, user_id):
    _seed_events(db, user_id, 5)

    def _fake_pi_ask(_db, _user_id, _question, **kwargs):
        callback = kwargs.get("on_event")
        if callable(callback):
            callback(AskEvent(type="thinking", content="Inspecting local context"))
            callback(
                AskEvent(
                    type="tool_call",
                    content="grep",
                    metadata={"input": {"pattern": "working on"}},
                )
            )
            callback(AskEvent(type="text", content="Working "))
            callback(AskEvent(type="text", content="on Syke."))
        return (
            "Working on Syke.",
            {
                "backend": "pi",
                "cost_usd": 0.01,
                "duration_ms": 500,
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "tool_calls": 1,
                "num_turns": 1,
                "provider": "azure-openai-responses",
                "model": "gpt-5.4-mini",
                "error": None,
            },
        )

    events: list[AskEvent] = []
    with patch("syke.llm.backends.pi_ask.pi_ask", side_effect=_fake_pi_ask):
        result, _cost = run_ask_stream(db, user_id, "What am I working on?", events.append)

    assert result == "Working on Syke."
    assert [event.content for event in events if event.type == "thinking"] == [
        "Inspecting local context"
    ]
    assert [event.content for event in events if event.type == "tool_call"] == ["grep"]
    assert [event.content for event in events if event.type == "text"] == [
        "Working ",
        "on Syke.",
    ]


def test_ask_json_outputs_structured_result(cli_runner) -> None:
    from syke.llm.providers import PROVIDERS

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["codex"]),
        patch(
            "syke.llm.pi_runtime.run_ask",
            return_value=(
                "Working on Syke.",
                {
                    "provider": "codex",
                    "duration_ms": 123,
                    "cost_usd": 0.02,
                    "input_tokens": 11,
                    "output_tokens": 22,
                    "tool_calls": 1,
                    "error": None,
                },
            ),
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "--json", "What am I doing?"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["question"] == "What am I doing?"
    assert payload["answer"] == "Working on Syke."
    assert payload["provider"] == "codex"
    assert payload["tool_calls"] == 1


def test_ask_jsonl_streams_events_and_result(cli_runner) -> None:
    from syke.llm.providers import PROVIDERS

    def _fake_run_ask(*, db, user_id, question, on_event):
        del db, user_id, question
        on_event(AskEvent(type="thinking", content="Inspecting"))
        on_event(
            AskEvent(
                type="tool_call",
                content="search",
                metadata={"input": {"query": "current work"}},
            )
        )
        on_event(AskEvent(type="text", content="Working on Syke."))
        return (
            "Working on Syke.",
            {
                "provider": "codex",
                "duration_ms": 456,
                "cost_usd": 0.03,
                "input_tokens": 12,
                "output_tokens": 18,
                "tool_calls": 1,
                "error": None,
            },
        )

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["codex"]),
        patch("syke.llm.pi_runtime.run_ask", side_effect=_fake_run_ask),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "ask", "--jsonl", "What am I doing?"])

    assert result.exit_code == 0
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert [row["type"] for row in rows] == ["thinking", "tool_call", "text", "result"]
    assert rows[-1]["answer"] == "Working on Syke."
    assert rows[-1]["provider"] == "codex"


# --- Setup: provider picker + synthesis removal ---


def test_provider_interactive_nontty_prints_inventory():
    from syke.cli import _setup_provider_interactive

    with (
        patch("syke.llm.codex_auth.read_codex_auth", return_value=None),
        patch("syke.llm.AuthStore") as MockStore,
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        store = MockStore.return_value
        store.get_active_provider.return_value = None
        store.get_token.return_value = None

        result = _setup_provider_interactive()

    assert result is False
    store.set_active_provider.assert_not_called()


def test_provider_interactive_nontty_no_autoselect_with_multiple_ready():
    from syke.cli import _setup_provider_interactive

    with (
        patch("syke.llm.codex_auth.read_codex_auth", return_value=MagicMock()),
        patch("syke.llm.AuthStore") as MockStore,
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.isatty.return_value = False
        store = MockStore.return_value
        store.get_active_provider.return_value = None
        store.get_token.return_value = None

        result = _setup_provider_interactive()

    assert result is False
    store.set_active_provider.assert_not_called()


def test_setup_runs_immediate_synthesis_on_cold_start(cli_runner, tmp_path):
    mock_db = MagicMock()
    mock_db.count_events.return_value = 10
    mock_db.get_memex.return_value = None

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.cli._build_setup_inspect_payload", return_value={
            "provider": {"configured": True, "id": "openai"},
            "sources": [],
            "trust": {"sources": [], "targets": []},
            "setup_targets": [],
            "daemon": {"platform": "Darwin", "installable": True, "detail": "ready"},
        }),
        patch("syke.cli._provider_payload", return_value={"configured": True, "id": "openai"}),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=[]),
        patch("syke.cli._observe_registry") as observe_registry,
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="~/.syke/bin/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="0.63.0"),
        patch(
            "syke.llm.backends.pi_synthesis.pi_synthesize",
            return_value={"status": "completed", "memex_updated": True, "num_turns": 7},
        ) as synth,
        patch.dict("os.environ", {"HOME": str(tmp_path)}),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        observe_registry.return_value.active_harnesses.return_value = []
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes", "--skip-daemon"])

    assert result.exit_code == 0
    synth.assert_called_once_with(mock_db, "test", force=True, first_run=True)


def test_setup_skips_immediate_synthesis_without_new_data_or_cold_start(cli_runner, tmp_path):
    mock_db = MagicMock()
    mock_db.count_events.return_value = 10
    mock_db.get_memex.return_value = {"content": "# Memex"}

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.cli._build_setup_inspect_payload", return_value={
            "provider": {"configured": True, "id": "openai"},
            "sources": [],
            "trust": {"sources": [], "targets": []},
            "setup_targets": [],
            "daemon": {"platform": "Darwin", "installable": True, "detail": "ready"},
        }),
        patch("syke.cli._provider_payload", return_value={"configured": True, "id": "openai"}),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=[]),
        patch("syke.cli._observe_registry") as observe_registry,
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="~/.syke/bin/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="0.63.0"),
        patch("syke.llm.backends.pi_synthesis.pi_synthesize") as synth,
        patch.dict("os.environ", {"HOME": str(tmp_path)}),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        observe_registry.return_value.active_harnesses.return_value = []
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes", "--skip-daemon"])

    assert result.exit_code == 0
    synth.assert_not_called()


def test_setup_bootstraps_adapters_before_ingest(cli_runner, tmp_path):
    from syke.observe.bootstrap import BootstrapResult

    mock_db = MagicMock()
    mock_db.count_events.return_value = 10
    mock_adapter = MagicMock()
    mock_adapter.ingest.return_value = MagicMock(events_count=2)
    mock_registry = MagicMock()
    mock_registry.active_harnesses.return_value = [MagicMock(source="claude-code")]
    mock_registry.get_adapter.return_value = mock_adapter

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.cli._setup_provider_interactive", return_value=True),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=[
            BootstrapResult("claude-code", "generated", "ok")
        ]) as bootstrap,
        patch("syke.observe.registry.HarnessRegistry", return_value=mock_registry),
        patch.dict("os.environ", {"HOME": str(tmp_path)}),
        patch("subprocess.run", side_effect=FileNotFoundError),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes", "--skip-daemon"])

    assert result.exit_code == 0
    bootstrap.assert_called_once_with("test")
    mock_adapter.ingest.assert_called_once_with()


def test_setup_json_is_inspect_only(cli_runner):
    with (
        patch("syke.cli.get_db") as get_db,
        patch("syke.cli._setup_provider_interactive") as provider_prompt,
        patch("syke.cli._setup_source_inventory", return_value=[]),
        patch("syke.cli._setup_provider_choices", return_value=[]),
        patch("syke.cli._trust_payload", return_value={"sources": [], "targets": []}),
        patch(
            "syke.cli._setup_runtime_payload",
            return_value={"launcher": "~/.syke/bin/pi", "installed": False, "ready": False},
        ),
        patch(
            "syke.cli._setup_daemon_viability_payload",
            return_value={"platform": "Darwin", "running": False, "registered": False, "installable": True},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["mode"] == "inspect"
    assert payload["schema_version"] == 1
    assert "provider_choices" in payload
    assert "trust" in payload
    assert "setup_targets" in payload
    get_db.assert_not_called()
    provider_prompt.assert_not_called()


def test_setup_requires_confirmation_before_mutating(cli_runner):
    with (
        patch(
            "syke.cli._provider_payload",
            return_value={"configured": True, "id": "openai", "auth_source": "~/.syke/auth.json"},
        ),
        patch("syke.cli._build_setup_inspect_payload", return_value={
            "provider": {"configured": True, "id": "openai", "auth_source": "~/.syke/auth.json"},
            "sources": [],
            "trust": {"sources": [], "targets": []},
            "daemon": {"platform": "Darwin", "installable": True, "detail": "ready"},
        }),
        patch("syke.llm.env.resolve_provider", return_value=SimpleNamespace(id="openai")),
        patch("syke.cli.get_db") as get_db,
        patch("syke.observe.bootstrap.ensure_adapters") as ensure_adapters,
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "--provider", "openai", "setup"],
            input="n\n",
        )

    assert result.exit_code == 0
    assert "Inspection only. No changes made." in result.output
    get_db.assert_not_called()
    ensure_adapters.assert_not_called()
    install_and_start.assert_not_called()


def test_setup_decline_happens_before_provider_selection(cli_runner):
    with (
        patch(
            "syke.cli._build_setup_inspect_payload",
            return_value={
                "provider": {"configured": False, "error": "provider not configured"},
                "sources": [],
                "trust": {"sources": [], "targets": []},
                "daemon": {"platform": "Darwin", "installable": True, "detail": "ready"},
            },
        ),
        patch("syke.cli._setup_provider_interactive") as provider_prompt,
        patch("syke.cli.get_db") as get_db,
    ):
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup"],
            input="n\n",
        )

    assert result.exit_code == 0
    assert "Inspection only. No changes made." in result.output
    provider_prompt.assert_not_called()
    get_db.assert_not_called()


def test_setup_keeps_active_provider_without_reprompting(cli_runner):
    mock_db = MagicMock()
    mock_db.count_events.return_value = 0

    provider_info = {
        "configured": True,
        "id": "azure",
        "auth_source": "~/.syke/auth.json",
        "model": "gpt-5.4-mini",
        "model_source": "config.toml providers.azure.model",
        "endpoint": "https://example.openai.azure.com",
        "endpoint_source": "config.toml providers.azure.endpoint",
    }

    with (
        patch(
            "syke.cli._build_setup_inspect_payload",
            return_value={
                "provider": provider_info,
                "sources": [],
                "trust": {"sources": [], "targets": []},
                "setup_targets": [],
                "daemon": {"platform": "Darwin", "installable": True, "detail": "ready"},
            },
        ),
        patch("syke.cli._provider_payload", return_value=provider_info),
        patch("syke.cli._setup_provider_interactive") as provider_prompt,
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.observe.bootstrap.ensure_adapters", return_value=[]),
        patch("syke.cli._observe_registry") as observe_registry,
        patch("syke.llm.pi_client.ensure_pi_binary", return_value="~/.syke/bin/pi"),
        patch("syke.llm.pi_client.get_pi_version", return_value="0.63.0"),
    ):
        observe_registry.return_value.active_harnesses.return_value = []
        result = cli_runner.invoke(
            cli,
            ["--user", "test", "setup", "--skip-daemon"],
            input="y\n",
        )

    assert result.exit_code == 0
    assert "Keeping active provider: azure" in result.output
    provider_prompt.assert_not_called()


def test_ingest_source_finds_generated_adapter_in_fresh_cli_state(cli_runner, tmp_path):
    user_id = "test-user"
    data_dir = tmp_path / ".syke-data"
    adapters_dir = data_dir / user_id / "adapters" / "claude-code"
    adapters_dir.mkdir(parents=True)
    _ = (adapters_dir / "adapter.py").write_text(
        "import json\n\ndef parse_line(line):\n    return json.loads(line)\n",
        encoding="utf-8",
    )

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _ = (sessions_dir / "session.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "session_id": "s1",
                        "timestamp": "2026-03-27T12:00:00",
                        "role": "user",
                        "content": "hello",
                        "event_type": "turn",
                    }
                ),
                json.dumps(
                    {
                        "session_id": "s1",
                        "timestamp": "2026-03-27T12:00:01",
                        "role": "assistant",
                        "content": "hi",
                        "event_type": "turn",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _ = (adapters_dir / "descriptor.toml").write_text(
        dedent(
            f"""
            [discover]
            roots = [{{ path = {str(sessions_dir)!r}, include = ["*.jsonl"], priority = 1 }}]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "syke.db"
    db = SykeDB(db_path)
    db.initialize()

    try:
        with (
            patch("syke.config.DATA_DIR", data_dir),
            patch("syke.cli.get_db", return_value=db),
        ):
            result = cli_runner.invoke(
                cli,
                ["--user", user_id, "ingest", "source", "claude-code", "--yes"],
            )

        assert result.exit_code == 0
        assert "claude-code ingestion complete" in result.output
        assert "2 events" in result.output

        verify_db = SykeDB(db_path)
        try:
            assert verify_db.count_events(user_id, source="claude-code") == 2
        finally:
            verify_db.close()
    finally:
        db.close()


# --- Auth Set (LiteLLM Providers) ---


class TestAuthSetLiteLLM:
    """Tests for 'syke auth set' command with LiteLLM providers."""

    def test_auth_set_azure_writes_config_and_auth(self, cli_runner):
        """Azure: stores endpoint+model in config, api_key in auth."""
        with (
            patch("syke.config_file.write_provider_config") as mock_write_config,
            patch("syke.llm.AuthStore") as MockStore,
        ):
            store = MockStore.return_value
            result = cli_runner.invoke(
                cli,
                [
                    "auth",
                    "set",
                    "azure",
                    "--api-key",
                    "sk-test-key",
                    "--endpoint",
                    "https://test.openai.azure.com",
                    "--model",
                    "gpt-4o",
                ],
            )

        assert result.exit_code == 0
        mock_write_config.assert_called_once_with(
            "azure",
            {"endpoint": "https://test.openai.azure.com", "model": "gpt-4o"},
        )
        store.set_token.assert_called_once_with("azure", "sk-test-key")

    def test_auth_set_openai_writes_config_and_auth(self, cli_runner):
        """OpenAI: stores model in config, api_key in auth."""
        with (
            patch("syke.config_file.write_provider_config") as mock_write_config,
            patch("syke.llm.AuthStore") as MockStore,
        ):
            store = MockStore.return_value
            result = cli_runner.invoke(
                cli,
                [
                    "auth",
                    "set",
                    "openai",
                    "--api-key",
                    "sk-test-key",
                    "--model",
                    "gpt-4o-mini",
                ],
            )

        assert result.exit_code == 0
        mock_write_config.assert_called_once_with("openai", {"model": "gpt-4o-mini"})
        store.set_token.assert_called_once_with("openai", "sk-test-key")

    def test_auth_set_ollama_no_api_key(self, cli_runner):
        """Ollama: stores model in config, no api_key needed."""
        with (
            patch("syke.config_file.write_provider_config") as mock_write_config,
            patch("syke.llm.AuthStore") as MockStore,
        ):
            store = MockStore.return_value
            result = cli_runner.invoke(
                cli,
                [
                    "auth",
                    "set",
                    "ollama",
                    "--model",
                    "llama3.2",
                ],
            )

        assert result.exit_code == 0
        mock_write_config.assert_called_once_with("ollama", {"model": "llama3.2"})
        store.set_token.assert_not_called()

    def test_auth_set_azure_with_use_flag(self, cli_runner):
        """Azure with --use: sets as active provider after storing."""
        with (
            patch("syke.config_file.write_provider_config") as mock_write_config,
            patch("syke.llm.AuthStore") as MockStore,
        ):
            store = MockStore.return_value
            result = cli_runner.invoke(
                cli,
                [
                    "auth",
                    "set",
                    "azure",
                    "--api-key",
                    "sk-test-key",
                    "--endpoint",
                    "https://test.openai.azure.com",
                    "--model",
                    "gpt-4o",
                    "--use",
                ],
            )

        assert result.exit_code == 0
        mock_write_config.assert_called_once()
        store.set_token.assert_called_once()
        store.set_active_provider.assert_called_once_with("azure")

    def test_auth_set_azure_with_use_rejects_missing_endpoint(self, cli_runner):
        fake_cfg = SimpleNamespace(providers={}, models=SimpleNamespace(synthesis=""))
        with (
            patch("syke.config.CFG", fake_cfg),
            patch("syke.llm.AuthStore") as MockStore,
        ):
            store = MockStore.return_value
            result = cli_runner.invoke(
                cli,
                [
                    "auth",
                    "set",
                    "azure",
                    "--api-key",
                    "sk-test-key",
                    "--use",
                ],
            )

        assert result.exit_code == 1
        store.set_token.assert_called_once_with("azure", "sk-test-key")
        store.set_active_provider.assert_not_called()


def test_auth_use_rejects_provider_missing_required_runtime_fields(cli_runner) -> None:
    fake_cfg = SimpleNamespace(providers={}, models=SimpleNamespace(synthesis=""))
    with (
        patch("syke.config.CFG", fake_cfg),
        patch("syke.llm.AuthStore") as MockStore,
    ):
        store = MockStore.return_value
        result = cli_runner.invoke(cli, ["auth", "use", "azure"])

    assert result.exit_code == 1
    assert "missing" in result.output.lower()
    store.set_active_provider.assert_not_called()


def test_auth_status_json_includes_tokenless_active_provider_from_config(cli_runner) -> None:
    fake_cfg = SimpleNamespace(
        providers={"vllm": {"base_url": "http://127.0.0.1:8000/v1", "model": "mistral"}},
        models=SimpleNamespace(synthesis="mistral"),
    )

    with (
        patch("syke.config.CFG", fake_cfg),
        patch("syke.llm.AuthStore") as MockStore,
        patch("syke.llm.codex_auth.read_codex_auth", return_value=None),
    ):
        store = MockStore.return_value
        store.get_active_provider.return_value = "vllm"
        store.get_token.return_value = None
        store.list_providers.return_value = {}
        result = cli_runner.invoke(cli, ["auth", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    configured = {entry["id"] for entry in payload["configured_providers"]}
    assert "vllm" in configured


def test_auth_status_json_shows_selected_and_configured_runtime(cli_runner) -> None:
    from syke.llm.providers import PROVIDERS

    fake_cfg = SimpleNamespace(
        providers={"openai": {"model": "gpt-5.4", "base_url": "https://proxy.example/v1"}},
        models=SimpleNamespace(synthesis="gpt-5-mini"),
    )

    with (
        patch("syke.config.CFG", fake_cfg),
        patch("syke.llm.AuthStore") as MockStore,
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["openai"]),
        patch("syke.llm.codex_auth.read_codex_auth", return_value=None),
    ):
        store = MockStore.return_value
        store.get_active_provider.return_value = "openai"
        store.get_token.return_value = "sk-test"
        store.list_providers.return_value = {
            "openai": {"credential": "●●● (7 chars)", "active": "yes"}
        }
        result = cli_runner.invoke(cli, ["auth", "status", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["active_provider"] == "openai"
    assert payload["selected_provider"]["id"] == "openai"
    assert payload["selected_provider"]["auth_source"] == "~/.syke/auth.json"
    assert payload["selected_provider"]["model"] == "gpt-5.4"
    assert payload["selected_provider"]["endpoint"] == "https://proxy.example/v1"
    assert payload["configured_providers"][0]["runtime_provider"] == "openai"
