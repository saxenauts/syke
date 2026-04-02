from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from syke.cli import _wait_for_daemon_startup, cli


def test_daemon_start_reports_unhealthy_registration_without_success(cli_runner) -> None:
    with (
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli._wait_for_daemon_startup",
            return_value={
                "running": False,
                "registered": True,
                "pid": None,
                "ipc": {"ok": False, "detail": "daemon IPC socket missing"},
            },
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "start"])

    assert result.exit_code == 0
    assert "health is not confirmed yet" in result.output
    assert "Daemon started. Sync runs every" not in result.output


def test_daemon_stop_reports_incomplete_when_process_survives(cli_runner) -> None:
    with (
        patch("syke.daemon.daemon.is_running", side_effect=[(True, 123), (True, 123)]),
        patch(
            "syke.daemon.daemon.launchd_metadata",
            side_effect=[{"registered": True}, {"registered": False}],
        ),
        patch("syke.daemon.daemon.stop_and_unload"),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "stop"])

    assert result.exit_code == 0
    assert "Daemon stop is incomplete." in result.output


def test_self_update_uses_uv_tool_upgrade_for_uv_tool_installs(cli_runner) -> None:
    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli._detect_install_method", return_value="uv_tool"),
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value.returncode = 0
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert any(call.args[0] == ["uv", "tool", "upgrade", "syke"] for call in run_mock.call_args_list)


def test_self_update_aborts_when_daemon_does_not_stop_cleanly(cli_runner) -> None:
    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli._detect_install_method", return_value="uv_tool"),
        patch("syke.daemon.daemon.is_running", return_value=(True, 123)),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli._wait_for_daemon_shutdown",
            return_value={"running": True, "registered": False},
        ),
        patch("subprocess.run") as run_mock,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert "Daemon did not stop cleanly" in result.output
    run_mock.assert_not_called()


def test_self_update_reports_degraded_restart_truthfully(cli_runner) -> None:
    with (
        patch("syke.cli.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli._detect_install_method", return_value="uv_tool"),
        patch("syke.daemon.daemon.is_running", return_value=(True, 123)),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli._wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0)),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli._wait_for_daemon_startup",
            return_value={
                "platform": "Darwin",
                "running": True,
                "registered": True,
                "pid": 999,
                "ipc": {"ok": False, "detail": "daemon IPC socket missing"},
            },
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert "warm ask is not ready yet" in result.output


def test_wait_for_daemon_startup_requires_ipc_when_platform_is_darwin(monkeypatch) -> None:
    snapshots = iter(
        [
            {
                "platform": "Darwin",
                "running": True,
                "registered": True,
                "pid": 1,
                "ipc": {"ok": False, "detail": "missing"},
            },
            {
                "platform": "Darwin",
                "running": True,
                "registered": True,
                "pid": 1,
                "ipc": {"ok": False, "detail": "missing"},
            },
            {
                "platform": "Darwin",
                "running": True,
                "registered": True,
                "pid": 1,
                "ipc": {"ok": True, "detail": "present"},
            },
        ]
    )

    monkeypatch.setattr("syke.cli._daemon_readiness_snapshot", lambda _user: next(snapshots))
    monotonic_values = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr("time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("time.sleep", lambda _delay: None)

    snapshot = _wait_for_daemon_startup("test", timeout_seconds=1.0)

    assert snapshot["ipc"]["ok"] is True
