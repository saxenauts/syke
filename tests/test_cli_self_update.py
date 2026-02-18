"""Tests for the self-update CLI command."""

from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from syke.cli import cli


def test_self_update_already_current():
    """self-update exits cleanly when already on latest version."""
    runner = CliRunner()
    with patch("syke.version_check.get_latest_version", return_value="0.2.9"), \
         patch("syke.cli.__version__", "0.2.9"):
        result = runner.invoke(cli, ["--user", "test", "self-update"])
    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_self_update_network_failure():
    """self-update exits gracefully when PyPI is unreachable."""
    runner = CliRunner()
    with patch("syke.version_check.get_latest_version", return_value=None):
        result = runner.invoke(cli, ["--user", "test", "self-update"])
    assert result.exit_code == 0
    assert "PyPI" in result.output or "connection" in result.output.lower()


def test_self_update_source_install_exits_early():
    """self-update prints git instructions and exits for source installs."""
    runner = CliRunner()
    with patch("syke.version_check.get_latest_version", return_value="99.0.0"), \
         patch("syke.cli.__version__", "0.1.0"), \
         patch("syke.cli._detect_install_method", return_value="source"):
        result = runner.invoke(cli, ["--user", "test", "self-update", "--yes"])
    assert result.exit_code == 0
    assert "git pull" in result.output


def test_self_update_uvx_exits_early():
    """self-update prints uvx note and exits for uvx installs."""
    runner = CliRunner()
    with patch("syke.version_check.get_latest_version", return_value="99.0.0"), \
         patch("syke.cli.__version__", "0.1.0"), \
         patch("syke.cli._detect_install_method", return_value="uvx"):
        result = runner.invoke(cli, ["--user", "test", "self-update", "--yes"])
    assert result.exit_code == 0
    assert "uvx" in result.output


def test_self_update_pipx_runs_upgrade():
    """self-update runs pipx upgrade syke for pipx installs."""
    runner = CliRunner()
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    with patch("syke.version_check.get_latest_version", return_value="99.0.0"), \
         patch("syke.cli.__version__", "0.1.0"), \
         patch("syke.cli._detect_install_method", return_value="pipx"), \
         patch("syke.daemon.daemon.is_running", return_value=(False, None)), \
         patch("subprocess.run", mock_run):
        result = runner.invoke(cli, ["--user", "test", "self-update", "--yes"])
    assert result.exit_code == 0
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("pipx" in c and "upgrade" in c for c in calls)


def test_self_update_pip_runs_upgrade():
    """self-update runs pip install --upgrade syke for pip installs."""
    runner = CliRunner()
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    with patch("syke.version_check.get_latest_version", return_value="99.0.0"), \
         patch("syke.cli.__version__", "0.1.0"), \
         patch("syke.cli._detect_install_method", return_value="pip"), \
         patch("syke.daemon.daemon.is_running", return_value=(False, None)), \
         patch("subprocess.run", mock_run):
        result = runner.invoke(cli, ["--user", "test", "self-update", "--yes"])
    assert result.exit_code == 0
    calls = mock_run.call_args_list
    assert any(
        call.args[0] == ["pip", "install", "--upgrade", "syke"]
        for call in calls
    )


def test_self_update_restarts_daemon_when_was_running():
    """self-update stops and restarts the daemon when it was running before upgrade."""
    runner = CliRunner()
    mock_run = MagicMock(return_value=MagicMock(returncode=0))
    mock_stop = MagicMock()
    mock_start = MagicMock()
    with patch("syke.version_check.get_latest_version", return_value="99.0.0"), \
         patch("syke.cli.__version__", "0.1.0"), \
         patch("syke.cli._detect_install_method", return_value="pipx"), \
         patch("syke.daemon.daemon.is_running", return_value=(True, 123)), \
         patch("syke.daemon.daemon.stop_and_unload", mock_stop), \
         patch("syke.daemon.daemon.install_and_start", mock_start), \
         patch("subprocess.run", mock_run):
        result = runner.invoke(cli, ["--user", "test", "self-update", "--yes"])
    assert result.exit_code == 0
    mock_stop.assert_called_once()
    mock_start.assert_called_once()
