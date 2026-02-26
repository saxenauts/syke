"""Tests for new CLI commands: dashboard, context, doctor, mcp serve."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from syke.cli import cli


# ---------------------------------------------------------------------------
# Feature A: bare `syke` shows dashboard (not Usage:)
# ---------------------------------------------------------------------------


def test_bare_syke_shows_dashboard():
    """Running `syke` without a subcommand shows status dashboard, not help."""
    runner = CliRunner()
    with (
        patch("syke.cli._claude_is_authenticated", return_value=False),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.cli.user_db_path", return_value=MagicMock(exists=lambda: False)),
        patch("syke.distribution.harness.status_all", return_value=[]),
    ):
        result = runner.invoke(cli, ["--user", "test"])
    assert result.exit_code == 0
    assert not result.output.strip().startswith("Usage:")
    assert "Syke" in result.output
    assert "Auth" in result.output
    assert "Daemon" in result.output


def test_bare_syke_dashboard_with_db(tmp_path):
    """Dashboard shows event count and memex status when DB exists."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_db.count_events.return_value = 42
    mock_db.get_status.return_value = {"latest_event_at": "2025-01-01T00:00:00"}
    mock_db.get_memex.return_value = {"content": "# Memex"}
    mock_db.count_memories.return_value = 5
    db_path = tmp_path / "syke.db"
    db_path.touch()
    with (
        patch("syke.cli._claude_is_authenticated", return_value=True),
        patch("syke.cli.user_db_path", return_value=db_path),
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.daemon.daemon.launchd_status", return_value='"LastExitStatus" = 0;'),
        patch("platform.system", return_value="Darwin"),
    ):
        result = runner.invoke(cli, ["--user", "test"])
    assert result.exit_code == 0
    assert "42" in result.output
    assert "synthesized" in result.output
    assert "5 memories" in result.output

# ---------------------------------------------------------------------------
# Feature B: `syke context`
# ---------------------------------------------------------------------------


def test_context_no_memex():
    """context prints 'No memex' when nothing is synthesized."""
    runner = CliRunner()
    mock_db = MagicMock()
    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.memory.memex.get_memex_for_injection", return_value=""),
    ):
        result = runner.invoke(cli, ["--user", "test", "context"])
    assert result.exit_code == 0
    assert "No memex" in result.output


def test_context_markdown_output():
    """context outputs memex content in markdown by default."""
    runner = CliRunner()
    mock_db = MagicMock()
    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.memory.memex.get_memex_for_injection", return_value="# My Memex\nHello world"),
    ):
        result = runner.invoke(cli, ["--user", "test", "context"])
    assert result.exit_code == 0
    assert "# My Memex" in result.output


def test_context_json_output():
    """context --format json outputs valid JSON."""
    import json

    runner = CliRunner()
    mock_db = MagicMock()
    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.memory.memex.get_memex_for_injection", return_value="hello"),
    ):
        result = runner.invoke(cli, ["--user", "test", "context", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["memex"] == "hello"
    assert data["user"] == "test"


# ---------------------------------------------------------------------------
# Feature C: `syke doctor`
# ---------------------------------------------------------------------------


def test_doctor_outputs_checks():
    """doctor shows OK/FAIL lines for each component."""
    runner = CliRunner()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("syke.cli._claude_is_authenticated", return_value=True),
        patch("syke.cli.user_db_path", return_value=MagicMock(exists=lambda: False)),
        patch("syke.daemon.daemon.launchd_status", return_value=None),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
    ):
        result = runner.invoke(cli, ["--user", "test", "doctor"])
    assert result.exit_code == 0
    assert "OK" in result.output
    assert "FAIL" in result.output  # DB missing + daemon stopped
    assert "Claude binary" in result.output
    assert "Claude auth" in result.output
    assert "Database" in result.output
    assert "Daemon" in result.output


def test_doctor_all_failing():
    """doctor with nothing configured shows all FAIL."""
    runner = CliRunner()
    with (
        patch("shutil.which", return_value=None),
        patch("syke.cli._claude_is_authenticated", return_value=False),
        patch("syke.cli.user_db_path", return_value=MagicMock(exists=lambda: False)),
        patch("syke.daemon.daemon.launchd_status", return_value=None),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.distribution.harness.status_all", return_value=[]),
    ):
        result = runner.invoke(cli, ["--user", "test", "doctor"])
    assert result.exit_code == 0
    # All four checks should be FAIL
    assert result.output.count("FAIL") == 4


# ---------------------------------------------------------------------------
# Help output shows all new commands
# ---------------------------------------------------------------------------


def test_help_shows_new_commands():
    """--help lists context and doctor."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ["context", "doctor"]:
        assert cmd in result.output, f"{cmd} missing from help output"



# ---------------------------------------------------------------------------
# Feature D: `syke record`
# ---------------------------------------------------------------------------


def test_record_plain_text():
    """record with a text argument writes an event via IngestGateway."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push.return_value = {"status": "ok", "event_id": "abcd1234-5678", "duplicate": False}

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record", "Prefers dark mode"])
    assert result.exit_code == 0
    assert "Recorded" in result.output
    assert "abcd1234" in result.output
    mock_gw.push.assert_called_once()
    call_kwargs = mock_gw.push.call_args
    assert call_kwargs.kwargs["source"] == "manual"
    assert call_kwargs.kwargs["content"] == "Prefers dark mode"


def test_record_with_tags():
    """record --tag passes tags in metadata."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push.return_value = {"status": "ok", "event_id": "abcd1234-5678", "duplicate": False}

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record", "-t", "work", "-t", "pref", "Likes Python"])
    assert result.exit_code == 0
    call_kwargs = mock_gw.push.call_args.kwargs
    assert call_kwargs["metadata"] == {"tags": ["work", "pref"]}


def test_record_custom_source():
    """record --source sets the source label."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push.return_value = {"status": "ok", "event_id": "abcd1234-5678", "duplicate": False}

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record", "--source", "cursor", "Observation"])
    assert result.exit_code == 0
    assert mock_gw.push.call_args.kwargs["source"] == "cursor"


def test_record_stdin(monkeypatch):
    """record reads from stdin when no text argument given."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push.return_value = {"status": "ok", "event_id": "abcd1234-5678", "duplicate": False}

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record"], input="Long research dump\nWith multiple lines")
    assert result.exit_code == 0
    assert "Recorded" in result.output
    content = mock_gw.push.call_args.kwargs["content"]
    assert "Long research dump" in content
    assert "multiple lines" in content


def test_record_duplicate():
    """record shows duplicate message when gateway returns duplicate."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push.return_value = {"status": "duplicate", "event_id": "abc", "duplicate": True}

    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record", "Same event"])
    assert result.exit_code == 0
    assert "duplicate" in result.output.lower()


def test_record_empty_fails():
    """record with no text and no stdin prints error."""
    runner = CliRunner()
    mock_db = MagicMock()

    with patch("syke.cli.get_db", return_value=mock_db):
        result = runner.invoke(cli, ["--user", "test", "record"])
    assert result.exit_code != 0
    assert "Nothing to record" in result.output


def test_record_json_mode():
    """record --json parses a JSON event."""
    import json

    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push.return_value = {"status": "ok", "event_id": "abcd1234-5678", "duplicate": False}

    ev = json.dumps({"text": "JSON observation", "tags": ["test"]})
    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record", "--json", ev])
    assert result.exit_code == 0
    assert "Recorded" in result.output
    call_kwargs = mock_gw.push.call_args.kwargs
    assert call_kwargs["content"] == "JSON observation"


def test_record_jsonl_batch():
    """record --jsonl reads multiple events from stdin."""
    import json

    runner = CliRunner()
    mock_db = MagicMock()
    mock_gw = MagicMock()
    mock_gw.push_batch.return_value = {"status": "ok", "inserted": 2, "duplicates": 0, "filtered": 0, "errors": [], "total": 2}

    lines = "\n".join([
        json.dumps({"source": "test", "event_type": "note", "title": "A", "content": "First"}),
        json.dumps({"source": "test", "event_type": "note", "title": "B", "content": "Second"}),
    ])
    with (
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.ingestion.gateway.IngestGateway", return_value=mock_gw),
    ):
        result = runner.invoke(cli, ["--user", "test", "record", "--jsonl"], input=lines)
    assert result.exit_code == 0
    assert "2" in result.output
    mock_gw.push_batch.assert_called_once()


def test_record_shows_in_help():
    """record appears in --help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "record" in result.output
