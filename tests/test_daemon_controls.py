from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from syke.cli_support.daemon_state import wait_for_daemon_startup
from syke.daemon.daemon import SykeDaemon
from syke.entrypoint import cli


def test_daemon_start_reports_unhealthy_registration_without_success(cli_runner) -> None:
    with (
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": False, "pid": None, "source": "none"},
        ),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_startup",
            return_value={
                "running": False,
                "registered": True,
                "platform": "Darwin",
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
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 123, "source": "pidfile"},
        ),
        patch("syke.daemon.daemon.launchd_metadata", return_value={"registered": True}),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_shutdown",
            return_value={"running": True, "registered": False, "pid": 123},
        ),
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "daemon", "stop"])

    assert result.exit_code == 0
    assert "Daemon stop is incomplete." in result.output


def test_self_update_uses_uv_tool_upgrade_for_uv_tool_installs(cli_runner) -> None:
    with (
        patch("syke.__version__", "0.1.0"),
        patch("syke.cli_commands.daemon.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli_commands.daemon.detect_install_method", return_value="uv_tool"),
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": False, "pid": None, "source": "none"},
        ),
        patch("subprocess.run") as run_mock,
    ):
        run_mock.return_value.returncode = 0
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert any(
        call.args[0] == ["uv", "tool", "upgrade", "syke"] for call in run_mock.call_args_list
    )


def test_self_update_aborts_when_daemon_does_not_stop_cleanly(cli_runner) -> None:
    with (
        patch("syke.__version__", "0.1.0"),
        patch("syke.cli_commands.daemon.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli_commands.daemon.detect_install_method", return_value="uv_tool"),
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 123, "source": "pidfile"},
        ),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_shutdown",
            return_value={"running": True, "registered": False},
        ),
        patch("subprocess.run") as run_mock,
    ):
        result = cli_runner.invoke(cli, ["--user", "test", "self-update", "--yes"])

    assert result.exit_code == 0
    assert "Daemon did not stop cleanly" in result.output
    assert all(
        call.args[0] != ["uv", "tool", "upgrade", "syke"] for call in run_mock.call_args_list
    )


def test_self_update_reports_degraded_restart_truthfully(cli_runner) -> None:
    with (
        patch("syke.__version__", "0.1.0"),
        patch("syke.cli_commands.daemon.__version__", "0.1.0"),
        patch("syke.version_check.check_update_available", return_value=(True, "99.0.0")),
        patch("syke.cli_commands.daemon.detect_install_method", return_value="uv_tool"),
        patch(
            "syke.daemon.daemon.daemon_process_state",
            return_value={"running": True, "pid": 123, "source": "pidfile"},
        ),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli_commands.daemon.daemon_state.wait_for_daemon_startup",
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

    monkeypatch.setattr(
        "syke.cli_support.daemon_state.daemon_readiness_snapshot", lambda _user: next(snapshots)
    )
    monotonic_values = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr("time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("time.sleep", lambda _delay: None)

    snapshot = wait_for_daemon_startup("test", timeout_seconds=1.0)

    assert snapshot["ipc"]["ok"] is True


def test_daemon_runtime_status_does_not_block_on_runtime_lock() -> None:
    daemon = SykeDaemon("test")
    daemon._pi_runtime = SimpleNamespace(
        status=lambda: {
            "alive": True,
            "provider": "kimi-coding",
            "model": "k2p5",
            "pid": 4242,
            "uptime_s": 12.0,
            "binding_error": None,
        }
    )

    daemon._runtime_lock.acquire()
    try:
        snapshot = daemon._handle_ipc_runtime_status()
    finally:
        daemon._runtime_lock.release()

    assert snapshot["alive"] is True
    assert snapshot["provider"] == "kimi-coding"
    assert snapshot["model"] == "k2p5"
    assert snapshot["busy"] is True


def test_daemon_cycle_skips_distribution_after_failed_synthesis() -> None:
    daemon = SykeDaemon("test")

    with (
        patch.object(daemon, "_health_check", return_value={"healthy": True}),
        patch.object(daemon, "_heal"),
        patch.object(daemon, "_synthesize", return_value={"status": "failed", "error": "429"}),
        patch.object(daemon, "_distribute") as distribute,
    ):
        daemon._daemon_cycle(SimpleNamespace())

    distribute.assert_not_called()


def test_daemon_distribute_passes_memex_updated_to_distribution() -> None:
    daemon = SykeDaemon("test")
    db = SimpleNamespace()

    with patch("syke.distribution.refresh_distribution") as refresh:
        refresh.return_value = SimpleNamespace(memex_path=None, skill_paths=[], warnings=[])

        # memex_updated=True → forwarded as True
        daemon._distribute(db, {"status": "completed", "memex_updated": True})
        assert refresh.call_args.kwargs["memex_updated"] is True

        # memex_updated=False → forwarded as False
        refresh.reset_mock()
        daemon._distribute(db, {"status": "completed", "memex_updated": False})
        assert refresh.call_args.kwargs["memex_updated"] is False

        # key missing → defaults to False (not True)
        refresh.reset_mock()
        daemon._distribute(db, {"status": "completed"})
        assert refresh.call_args.kwargs["memex_updated"] is False


def test_daemon_ensure_process_markers_rewrites_pid_and_rebinds_ipc(tmp_path, monkeypatch) -> None:
    daemon = SykeDaemon("test")
    pid_path = tmp_path / "daemon.pid"
    socket_path = tmp_path / "daemon.sock"
    stop_ipc = Mock()

    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", pid_path)

    daemon._ipc_server = SimpleNamespace(
        socket_path=socket_path,
        stop=stop_ipc,
    )

    with patch.object(daemon, "_start_ipc_server") as start_ipc:
        daemon._ensure_process_markers()

    assert pid_path.exists()
    assert pid_path.read_text(encoding="utf-8").strip().isdigit()
    stop_ipc.assert_called_once()
    start_ipc.assert_called_once()


def test_synthesis_timeout_returns_failure() -> None:
    """If _runtime_lock can't be acquired within timeout, synthesis fails cleanly."""
    import threading

    daemon = SykeDaemon("test")

    # Replace with a lock that's already held and times out immediately
    real_lock = threading.Lock()
    real_lock.acquire()
    daemon._runtime_lock = real_lock

    try:
        with patch("syke.llm.backends.pi_synthesis.pi_synthesize") as mock_synth:
            # _synthesize will try acquire(timeout=600) but we mock time:
            # Instead, just replace the lock's acquire to return False
            daemon._runtime_lock = SimpleNamespace(
                acquire=lambda timeout=None: False,
                release=lambda: None,
            )
            result = daemon._synthesize(SimpleNamespace(), 0)
    finally:
        real_lock.release()

    mock_synth.assert_not_called()
    assert result["status"] == "failed"
    assert "timeout" in result["error"]
