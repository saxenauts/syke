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
        patch("syke.cli.user_data_dir", return_value=MagicMock(**{"__truediv__": lambda self, x: MagicMock(exists=lambda: False)})),
    ):
        result = runner.invoke(cli, ["--user", "test"])
    assert result.exit_code == 0
    assert not result.output.strip().startswith("Usage:")
    assert "Syke" in result.output
    assert "Auth" in result.output
    assert "Daemon" in result.output


def test_bare_syke_dashboard_with_db(tmp_path):
    """Dashboard shows event count when DB exists."""
    runner = CliRunner()
    mock_db = MagicMock()
    mock_db.count_events.return_value = 42
    mock_db.get_status.return_value = {"latest_event_at": "2025-01-01T00:00:00"}
    db_path = tmp_path / "syke.db"
    db_path.touch()
    with (
        patch("syke.cli._claude_is_authenticated", return_value=True),
        patch("syke.daemon.daemon.is_running", return_value=(True, 1234)),
        patch("syke.cli.user_db_path", return_value=db_path),
        patch("syke.cli.get_db", return_value=mock_db),
        patch("syke.cli.user_data_dir", return_value=MagicMock(**{"__truediv__": lambda self, x: MagicMock(exists=lambda: False)})),
    ):
        result = runner.invoke(cli, ["--user", "test"])
    assert result.exit_code == 0
    assert "42" in result.output
    assert "1234" in result.output


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
