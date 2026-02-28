from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from syke.cli import _claude_is_authenticated, cli
from syke.distribution.ask_agent import AskEvent, _local_fallback, ask, ask_stream
from syke.memory.memex import update_memex
from syke.models import Event


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
def test_dashboard_shows_status_when_invoked_without_subcommand(
    cli_runner, tmp_path, has_db
):
    mock_db = MagicMock()
    mock_db.count_events.return_value = 42
    mock_db.get_status.return_value = {"latest_event_at": "2025-01-01T00:00:00"}
    mock_db.get_memex.return_value = {"content": "# Memex"}
    mock_db.count_memories.return_value = 5

    db_path = tmp_path / "syke.db"
    if has_db:
        db_path.touch()

    with (
        patch("syke.cli._claude_is_authenticated", return_value=has_db),
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
    assert "Auth" in result.output
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
def test_context_outputs_expected_format(
    cli_runner, fmt, memex, expected_text, is_json
):
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
    [(True, True, False, 2), (False, False, False, 4)],
    ids=["mixed_checks", "all_failing"],
)
def test_doctor_reports_expected_failures(
    cli_runner, has_binary, has_auth, has_db, expected_fail_count
):
    with (
        patch("shutil.which", return_value="/usr/bin/claude" if has_binary else None),
        patch("syke.cli._claude_is_authenticated", return_value=has_auth),
        patch("syke.cli.user_db_path", return_value=MagicMock(exists=lambda: has_db)),
        patch("syke.daemon.daemon.launchd_status", return_value=None),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.distribution.harness.status_all", return_value=[]),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "doctor"])

    assert result.exit_code == 0
    assert result.output.count("FAIL") == expected_fail_count
    assert "Claude binary" in result.output
    assert "Claude auth" in result.output
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
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gateway),
    ):
        result = cli_runner.invoke(
            cli, ["--user", "test", "record", "Prefers dark mode"]
        )

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
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gateway),
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
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gateway),
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


@pytest.mark.parametrize(
    "scenario", ["duplicate", "empty"], ids=["duplicate", "empty_input"]
)
def test_record_handles_duplicate_and_empty_inputs(cli_runner, scenario):
    mock_gateway = MagicMock()
    mock_gateway.push.return_value = {
        "status": "duplicate",
        "event_id": "abc",
        "duplicate": True,
    }

    with (
        patch("syke.cli.get_db", return_value=MagicMock()),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gateway),
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
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gateway),
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
def test_self_update_handles_current_or_network_cases(
    cli_runner, check_result, expected_text
):
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
def test_self_update_exits_early_for_source_and_uvx(
    cli_runner, install_method, expected_text
):
    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch(
            "syke.version_check.check_update_available", return_value=(True, "99.0.0")
        ),
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
        patch(
            "syke.version_check.check_update_available", return_value=(True, "99.0.0")
        ),
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
        patch(
            "syke.version_check.check_update_available", return_value=(True, "99.0.0")
        ),
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
        result, cost = ask(db, user_id, "What is the user working on?")
    else:
        events: list[AskEvent] = []
        result, cost = ask_stream(
            db, user_id, "What is the user working on?", events.append
        )

    assert "no data" in result.lower()
    assert cost == {}


def test_ask_returns_answer_with_mocked_client(db, user_id, mock_ask_client):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    _seed_events(db, user_id, 5)

    msg = MagicMock(spec=AssistantMessage)
    block = MagicMock(spec=TextBlock)
    block.text = "They are building Syke for a hackathon."
    msg.content = [block]

    result_msg = MagicMock(spec=ResultMessage)
    result_msg.total_cost_usd = 0.0
    result_msg.num_turns = 1
    result_msg.duration_api_ms = 100
    result_msg.usage = {"input_tokens": 10, "output_tokens": 20}

    _, patcher = mock_ask_client(responses=[msg, result_msg])
    with patcher:
        result, _cost = ask(db, user_id, "What is the user working on?")

    assert "Syke" in result


@pytest.mark.parametrize(
    "error_kind", ["generic", "sdk"], ids=["generic_error", "sdk_error"]
)
def test_ask_errors_return_local_fallback(db, user_id, mock_ask_client, error_kind):
    from claude_agent_sdk import ClaudeSDKError

    _seed_events(db, user_id, 3)

    error = RuntimeError("Agent SDK not available")
    if error_kind == "sdk":
        error = ClaudeSDKError("Connection failed")

    _, patcher = mock_ask_client(error=error)
    with patcher:
        result, _cost = ask(db, user_id, "What is happening?")

    assert result.strip() != ""
    assert "ask() failed" not in result
    assert ("fallback" in result.lower()) or ("no answer" in result.lower())


def test_ask_rate_limit_unknown_event_returns_partial_answer(db, user_id):
    from claude_agent_sdk import AssistantMessage, ClaudeSDKError, TextBlock

    _seed_events(db, user_id, 3)

    partial_text = "They are building Syke."

    async def _fake_receive():
        msg = MagicMock(spec=AssistantMessage)
        block = MagicMock(spec=TextBlock)
        block.text = partial_text
        msg.content = [block]
        yield msg
        raise ClaudeSDKError("Unknown message type: rate_limit_event")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.query = AsyncMock()
    mock_client.receive_response = _fake_receive

    with patch("syke.distribution.ask_agent.ClaudeSDKClient", return_value=mock_client):
        result, _cost = ask(db, user_id, "What is happening?")

    assert partial_text in result


def test_ask_empty_response_triggers_fallback(db, user_id, mock_ask_client):
    _seed_events(db, user_id, 3)

    _, patcher = mock_ask_client(responses=[])
    with patcher:
        result, _cost = ask(db, user_id, "What is happening?")

    assert result.strip() != ""
    assert ("fallback" in result.lower()) or ("no answer" in result.lower())


@pytest.mark.parametrize("has_memex", [True, False], ids=["with_memex", "no_memex"])
def test_local_fallback_uses_memex_when_available(db, user_id, has_memex):
    if has_memex:
        _seed_events(db, user_id, 3)
        update_memex(db, user_id, "# Memex\nUser is a Python developer.")

    result = _local_fallback(db, user_id, "what does the user do?")

    if has_memex:
        assert "Python developer" in result
        assert "fallback" in result.lower()
    else:
        assert "No data yet" in result or "No answer available" in result


def test_ask_stream_emits_tool_call_event(db, user_id, mock_ask_client):
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )

    _seed_events(db, user_id, 5)

    tool_msg = MagicMock(spec=AssistantMessage)
    tool_block = MagicMock(spec=ToolUseBlock)
    tool_block.name = "search_memories"
    tool_block.input = {"query": "working on"}
    tool_msg.content = [tool_block]

    answer_msg = MagicMock(spec=AssistantMessage)
    answer_block = MagicMock(spec=TextBlock)
    answer_block.text = "Working on Syke."
    answer_msg.content = [answer_block]

    result_msg = MagicMock(spec=ResultMessage)
    result_msg.total_cost_usd = 0.01
    result_msg.num_turns = 2
    result_msg.duration_api_ms = 500
    result_msg.usage = {"input_tokens": 10, "output_tokens": 20}

    _, patcher = mock_ask_client(responses=[tool_msg, answer_msg, result_msg])

    events: list[AskEvent] = []
    with patcher:
        result, _cost = ask_stream(db, user_id, "What am I working on?", events.append)

    assert "Working on Syke" in result
    tool_events = [event for event in events if event.type == "tool_call"]
    assert len(tool_events) == 1
    assert tool_events[0].content == "search_memories"


def test_help_shows_new_commands(cli_runner):
    result = cli_runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "context" in result.output
    assert "doctor" in result.output
    assert "record" in result.output
