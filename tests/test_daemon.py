"""Tests for daemon-related functionality."""

import os
import signal
from unittest.mock import patch, MagicMock

from syke.daemon.daemon import (
    SykeDaemon,
    _write_pid,
    _remove_pid,
    is_running,
    generate_plist,
    install_cron,
    uninstall_cron,
    cron_is_running,
    cron_status,
    install_and_start,
    stop_and_unload,
    get_status,
    PIDFILE,
)


def test_daemon_pid_lifecycle(tmp_path):
    """PID file is written and cleaned up correctly."""
    test_pidfile = tmp_path / "daemon.pid"
    with patch("syke.daemon.daemon.PIDFILE", test_pidfile):
        # Initially not running
        running, pid = is_running()
        assert running is False
        assert pid is None

        # Write PID
        _write_pid()
        assert test_pidfile.exists()
        written_pid = int(test_pidfile.read_text().strip())
        assert written_pid == os.getpid()

        # Should detect as running (our own PID)
        running, pid = is_running()
        assert running is True
        assert pid == os.getpid()

        # Remove PID
        _remove_pid()
        assert not test_pidfile.exists()

        # No longer running
        running, pid = is_running()
        assert running is False


def test_daemon_stale_pid_cleanup(tmp_path):
    """Stale PID file (dead process) is cleaned up automatically."""
    test_pidfile = tmp_path / "daemon.pid"
    # Write a PID that definitely doesn't exist (99999999)
    test_pidfile.write_text("99999999")

    with patch("syke.daemon.daemon.PIDFILE", test_pidfile):
        running, pid = is_running()
        assert running is False
        assert pid is None
        # PID file should be cleaned up
        assert not test_pidfile.exists()


def test_daemon_signal_stops_loop():
    """Daemon loop stops when signal handler sets running=False."""
    d = SykeDaemon("test", interval=1)
    assert d.running is True
    d._handle_signal(signal.SIGTERM, None)
    assert d.running is False


def test_generate_plist_source_install():
    """Source install plist uses sys.executable -m syke with WorkingDirectory."""
    import sys

    plist = generate_plist("testuser", source_install=True)
    assert "com.syke.daemon" in plist
    assert "testuser" in plist
    assert "<string>sync</string>" in plist
    assert "<?xml" in plist
    assert "StartInterval" in plist
    assert f"<string>{sys.executable}</string>" in plist
    assert "<string>-m</string>" in plist
    assert "WorkingDirectory" in plist


def test_generate_plist_pip_install():
    """Pip install plist uses syke console script, no -m, no WorkingDirectory."""
    plist = generate_plist("testuser", source_install=False)
    assert "com.syke.daemon" in plist
    assert "testuser" in plist
    assert "<string>sync</string>" in plist
    assert "<string>-m</string>" not in plist
    assert "WorkingDirectory" not in plist
    # Should reference the syke binary
    assert "syke" in plist


def test_generate_plist_with_api_key(monkeypatch):
    """API key is injected into plist EnvironmentVariables when set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123")
    plist = generate_plist("testuser", source_install=False)
    assert "ANTHROPIC_API_KEY" in plist
    assert "sk-ant-test-key-123" in plist
    assert "EnvironmentVariables" in plist


def test_generate_plist_no_api_key(monkeypatch):
    """No API key block when ANTHROPIC_API_KEY is unset."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plist = generate_plist("testuser", source_install=False)
    assert "ANTHROPIC_API_KEY" not in plist
    assert "EnvironmentVariables" not in plist


# --- cron backend tests ---


def test_install_cron_writes_entry(monkeypatch):
    """install_cron appends a tagged crontab entry."""
    mock_run = MagicMock()
    # First call: crontab -l returns existing crontab
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="0 * * * * echo existing\n"),  # crontab -l
        MagicMock(returncode=0),  # crontab - (write)
    ]
    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    install_cron("testuser", interval=900)

    # Second call should write new crontab with syke-daemon tag
    write_call = mock_run.call_args_list[1]
    written_input = write_call.kwargs.get("input", "") or write_call[1].get("input", "")
    assert "# syke-daemon" in written_input
    assert "testuser" in written_input
    assert "sync" in written_input
    assert "0 * * * * echo existing" in written_input  # preserves existing


def test_install_cron_no_existing_crontab(monkeypatch):
    """install_cron works when user has no existing crontab."""
    mock_run = MagicMock()
    mock_run.side_effect = [
        MagicMock(returncode=1, stdout="", stderr="no crontab for user"),  # crontab -l fails
        MagicMock(returncode=0),  # crontab - (write)
    ]
    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    install_cron("testuser")

    write_call = mock_run.call_args_list[1]
    written_input = write_call.kwargs.get("input", "") or write_call[1].get("input", "")
    assert "# syke-daemon" in written_input
    assert "testuser" in written_input


def test_uninstall_cron_removes_entry(monkeypatch):
    """uninstall_cron filters out syke-daemon lines from crontab."""
    existing = "0 * * * * echo existing\n*/15 * * * * syke --user bob sync # syke-daemon\n"
    mock_run = MagicMock()
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=existing),  # crontab -l
        MagicMock(returncode=0),  # crontab - (write)
    ]
    monkeypatch.setattr("subprocess.run", mock_run)

    result = uninstall_cron()
    assert result is True

    write_call = mock_run.call_args_list[1]
    written_input = write_call.kwargs.get("input", "") or write_call[1].get("input", "")
    assert "syke-daemon" not in written_input
    assert "echo existing" in written_input


def test_uninstall_cron_no_entry(monkeypatch):
    """uninstall_cron returns False when no syke-daemon entry exists."""
    mock_run = MagicMock()
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout="0 * * * * echo existing\n"),
    ]
    monkeypatch.setattr("subprocess.run", mock_run)

    result = uninstall_cron()
    assert result is False


# --- cron status tests ---


def test_cron_is_running_true(monkeypatch):
    """cron_is_running returns (True, None) when syke-daemon entry exists."""
    mock_run = MagicMock(return_value=MagicMock(
        returncode=0, stdout="*/15 * * * * syke sync # syke-daemon\n"
    ))
    monkeypatch.setattr("subprocess.run", mock_run)

    found, pid = cron_is_running()
    assert found is True
    assert pid is None


def test_cron_is_running_false(monkeypatch):
    """cron_is_running returns (False, None) when no syke-daemon entry."""
    mock_run = MagicMock(return_value=MagicMock(
        returncode=0, stdout="0 * * * * echo hello\n"
    ))
    monkeypatch.setattr("subprocess.run", mock_run)

    found, pid = cron_is_running()
    assert found is False
    assert pid is None


def test_cron_is_running_no_crontab(monkeypatch):
    """cron_is_running returns (False, None) when crontab command fails."""
    mock_run = MagicMock(side_effect=FileNotFoundError("crontab not found"))
    monkeypatch.setattr("subprocess.run", mock_run)

    found, pid = cron_is_running()
    assert found is False
    assert pid is None


# --- platform dispatch tests ---


def test_platform_dispatch_darwin(monkeypatch):
    """install_and_start calls launchd on macOS."""
    monkeypatch.setattr("sys.platform", "darwin")
    mock_launchd = MagicMock()
    monkeypatch.setattr("syke.daemon.daemon.install_launchd", mock_launchd)

    install_and_start("testuser", interval=900)
    mock_launchd.assert_called_once_with("testuser")


def test_platform_dispatch_linux(monkeypatch):
    """install_and_start calls cron on Linux."""
    monkeypatch.setattr("sys.platform", "linux")
    mock_cron = MagicMock()
    monkeypatch.setattr("syke.daemon.daemon.install_cron", mock_cron)

    install_and_start("testuser", interval=600)
    mock_cron.assert_called_once_with("testuser", interval=600)


def test_stop_dispatch_darwin(monkeypatch):
    """stop_and_unload calls uninstall_launchd on macOS."""
    monkeypatch.setattr("sys.platform", "darwin")
    mock_uninstall = MagicMock()
    monkeypatch.setattr("syke.daemon.daemon.uninstall_launchd", mock_uninstall)

    stop_and_unload()
    mock_uninstall.assert_called_once()


def test_stop_dispatch_linux(monkeypatch):
    """stop_and_unload calls uninstall_cron on Linux."""
    monkeypatch.setattr("sys.platform", "linux")
    mock_uninstall = MagicMock()
    monkeypatch.setattr("syke.daemon.daemon.uninstall_cron", mock_uninstall)

    stop_and_unload()
    mock_uninstall.assert_called_once()


def test_get_status_linux(monkeypatch):
    """get_status shows cron status on Linux."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("syke.daemon.daemon.cron_is_running", lambda: (True, None))
    monkeypatch.setattr("syke.daemon.daemon.cron_status", lambda: "[green]Cron job installed[/green]")
    monkeypatch.setattr("syke.daemon.daemon.is_running", lambda: (False, None))

    status = get_status()
    assert "cron" in status.lower() or "green" in status.lower()


# --- plist interval + permissions tests ---


def test_generate_plist_uses_custom_interval():
    """generate_plist respects custom interval parameter."""
    plist = generate_plist("testuser", source_install=False)
    # Default is 900
    assert "<integer>900</integer>" in plist

    plist_custom = generate_plist("testuser", source_install=False, interval=600)
    assert "<integer>600</integer>" in plist_custom
    assert "<integer>900</integer>" not in plist_custom


def test_daemon_status_last_sync(monkeypatch):
    """daemon-status shows last sync time from metrics.jsonl."""
    from syke.daemon.metrics import MetricsTracker

    fake_summary = {
        "last_run": {
            "operation": "sync",
            "completed_at": "2026-02-18T14:32:45+00:00",
            "events_processed": 8,
            "success": True,
        }
    }
    monkeypatch.setattr(MetricsTracker, "get_summary", lambda self: fake_summary)

    tracker = MetricsTracker("testuser")
    summary = tracker.get_summary()
    last = summary.get("last_run")
    assert last is not None
    assert last["events_processed"] == 8
    assert "2026-02-18" in last["completed_at"]


def test_daemon_logs_reads_log_file(tmp_path, monkeypatch):
    """daemon-logs reads last N lines from daemon.log."""
    from collections import deque
    from syke.daemon.daemon import LOG_PATH

    log_content = "\n".join(
        f"2026-02-18 14:0{i}:00 SYNC  no new events" for i in range(10)
    ) + "\n"
    fake_log = tmp_path / "daemon.log"
    fake_log.write_text(log_content)

    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", fake_log)

    lines = list(deque(fake_log.read_text().splitlines(), maxlen=5))
    assert len(lines) == 5
    assert "SYNC" in lines[0]


def test_sync_cycle_log_format(monkeypatch):
    """_sync_cycle writes clean one-liner to stdout with full ISO timestamp, no ANSI."""
    import io
    from unittest.mock import patch, MagicMock
    from syke.daemon.daemon import SykeDaemon

    d = SykeDaemon("testuser", interval=900)

    mock_db = MagicMock()
    captured = io.StringIO()

    with patch("syke.db.SykeDB", return_value=mock_db), \
         patch("syke.config.user_db_path", return_value="/tmp/fake.db"), \
         patch("syke.sync.run_sync", return_value=(3, ["claude-code", "github"])), \
         patch("syke.version_check.check_update_available", return_value=(False, None)), \
         patch("sys.stdout", captured):
        d._sync_cycle()

    output = captured.getvalue()
    assert "\x1b[" not in output, "ANSI escape codes found in daemon log"
    assert len(output.strip().splitlines()) == 1, "Expected exactly one log line"
    line = output.strip()
    # Full ISO timestamp: YYYY-MM-DD HH:MM:SS
    assert len(line) > 19 and line[4] == "-" and line[7] == "-" and line[10] == " "
    assert "SYNC" in line
    assert "+3" in line
    assert "claude-code" in line


def test_install_launchd_sets_file_permissions(tmp_path, monkeypatch):
    """install_launchd sets plist to 600 permissions."""
    from syke.daemon.daemon import install_launchd

    plist_path = tmp_path / "com.syke.daemon.plist"
    log_path = tmp_path / "daemon.log"
    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)
    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", log_path)
    monkeypatch.setattr("subprocess.run", MagicMock())

    install_launchd("testuser")

    import stat
    mode = plist_path.stat().st_mode & 0o777
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


def test_sync_cycle_warns_on_update(monkeypatch):
    """_sync_cycle logs WARN and inserts deduped event when update available."""
    import io
    from unittest.mock import patch, MagicMock
    from syke.daemon.daemon import SykeDaemon

    d = SykeDaemon("testuser", interval=900)
    mock_db = MagicMock()
    captured = io.StringIO()

    with patch("syke.db.SykeDB", return_value=mock_db), \
         patch("syke.config.user_db_path", return_value="/tmp/fake.db"), \
         patch("syke.sync.run_sync", return_value=(0, [])), \
         patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")), \
         patch("sys.stdout", captured):
        d._sync_cycle()

    output = captured.getvalue()
    assert "WARN" in output
    assert "99.0.0" in output
    assert len(output.strip().splitlines()) == 2, "Expected exactly one SYNC line + one WARN line"
    mock_db.insert_event.assert_called_once()


def test_sync_cycle_noop_when_current(monkeypatch):
    """_sync_cycle does not log WARN or insert event when already up to date."""
    import io
    from unittest.mock import patch, MagicMock
    from syke.daemon.daemon import SykeDaemon

    d = SykeDaemon("testuser", interval=900)
    mock_db = MagicMock()
    captured = io.StringIO()

    with patch("syke.db.SykeDB", return_value=mock_db), \
         patch("syke.config.user_db_path", return_value="/tmp/fake.db"), \
         patch("syke.sync.run_sync", return_value=(0, [])), \
         patch("syke.version_check.check_update_available", return_value=(False, "0.2.9")), \
         patch("sys.stdout", captured):
        d._sync_cycle()

    output = captured.getvalue()
    assert "WARN" not in output
    mock_db.insert_event.assert_not_called()
