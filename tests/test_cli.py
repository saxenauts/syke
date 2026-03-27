from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from syke.cli import _claude_is_authenticated, cli
from syke.llm.backends import AskEvent
from syke.llm.runtime_switch import run_ask, run_ask_stream
from syke.models import Event

_BACKEND_PREFIX = "syke.llm." + "backends"
_CLAUDE_ASK_MODULE = _BACKEND_PREFIX + ".claude_ask"


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
        patch("syke.cli._claude_is_authenticated", return_value=has_db),
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["claude-login"]),
        patch(
            "syke.cli.user_db_path",
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


# --- Doctor ---


@pytest.mark.parametrize(
    "has_binary,has_auth,has_db,expected_fail_count",
    [(True, True, False, 2), (False, False, False, 2)],
    ids=["mixed_checks", "all_failing"],
)
def test_doctor_reports_expected_failures(
    cli_runner, has_binary, has_auth, has_db, expected_fail_count
):
    from syke.llm.providers import PROVIDERS

    with (
        patch("shutil.which", return_value="/usr/bin/claude" if has_binary else None),
        patch("syke.cli._claude_is_authenticated", return_value=has_auth),
        patch("syke.cli.user_db_path", return_value=MagicMock(exists=lambda: has_db)),
        patch("syke.daemon.daemon.launchd_status", return_value=None),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.distribution.harness.status_all", return_value=[]),
        patch("syke.llm.env.resolve_provider", return_value=PROVIDERS["claude-login"]),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "doctor"])

    assert result.exit_code == 0
    assert result.output.count("FAIL") == expected_fail_count
    assert "Provider" in result.output
    assert "Pi runtime" in result.output
    assert "Database" in result.output
    assert "Daemon" in result.output


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


# --- Auth ---


@pytest.mark.parametrize(
    "has_binary,has_claude_dir,has_credentials,expected",
    [
        (False, False, False, False),
        (True, False, False, False),
        (True, True, True, True),
    ],
    ids=["binary_missing", "claude_dir_absent", "credentials_present"],
)
def test_claude_is_authenticated_by_binary_directory_and_credentials(
    monkeypatch, tmp_path, has_binary, has_claude_dir, has_credentials, expected
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "shutil.which",
        lambda _: "/usr/local/bin/claude" if has_binary else None,
    )

    claude_dir = Path.home() / ".claude"
    if has_claude_dir:
        claude_dir.mkdir(parents=True, exist_ok=True)
    if has_credentials:
        (claude_dir / ("credentials" + ".json")).write_text("{}")

    assert _claude_is_authenticated() is expected


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
                    content="search_memories",
                    metadata={"input": {"query": "working on"}},
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
    assert [event.content for event in events if event.type == "tool_call"] == ["search_memories"]
    assert [event.content for event in events if event.type == "text"] == [
        "Working ",
        "on Syke.",
    ]


# --- Setup: provider picker + synthesis removal ---


def test_provider_interactive_nontty_prints_inventory():
    from syke.cli import _setup_provider_interactive

    with (
        patch("syke.llm.env._claude_login_available", return_value=True),
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
        patch("syke.llm.env._claude_login_available", return_value=True),
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


def test_setup_does_not_call_synthesize(cli_runner, tmp_path):
    """Setup must never call synthesize — synthesis is deferred to daemon's first sync."""
    mock_db = MagicMock()
    mock_db.count_events.return_value = 10

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.cli._setup_provider_interactive", return_value=True),
        patch.dict("os.environ", {"HOME": str(tmp_path)}),
        patch("subprocess.run", side_effect=FileNotFoundError),
        patch("syke.llm.runtime_switch.run_synthesis") as mock_synth,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "setup", "--yes", "--skip-daemon"])

    mock_synth.assert_not_called()
    assert result.exit_code == 0


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
