import inspect
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from syke.daemon.daemon import (
    LAUNCHD_LABEL,
    DaemonInstanceLocked,
    SykeDaemon,
    _acquire_daemon_lock,
    _is_tcc_protected,
    _pid_is_safe_daemon_target,
    _pid_looks_like_syke,
    _release_daemon_lock,
    _remove_pid,
    _write_pid,
    cron_is_running,
    daemon_process_state,
    generate_plist,
    install_and_start,
    install_cron,
    install_launchd,
    is_running,
    launchd_metadata,
    launchd_status,
    stop_and_unload,
    uninstall_cron,
    uninstall_launchd,
)
from syke.runtime.locator import SykeRuntimeDescriptor


def _call_with_supported_args(func, **kwargs):
    params = inspect.signature(func).parameters
    call_kwargs = {k: v for k, v in kwargs.items() if k in params}
    return func(**call_kwargs)


def _read_pid_value(pid_path):
    return Path(pid_path).read_text(encoding="utf-8").strip()


# --- PID lifecycle ---


def test_daemon_pid_lifecycle_write_read_remove(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    _write_pid()
    assert pid_path.exists()
    assert _read_pid_value(pid_path).isdigit()

    with patch("os.kill", return_value=None):
        running, _pid = is_running()
        assert running is True

    _remove_pid()
    assert not pid_path.exists()


def test_daemon_stale_pid_cleanup(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    pid_path.write_text("91919", encoding="utf-8")
    assert pid_path.exists()

    with patch("os.kill", side_effect=ProcessLookupError):
        running, _ = is_running()
        assert running is False

    assert not pid_path.exists()


def test_daemon_pid_permission_denied_treated_as_running(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    pid_path.write_text("91919", encoding="utf-8")

    with patch("os.kill", side_effect=PermissionError):
        running, pid = is_running()

    assert running is True
    assert pid == 91919
    assert pid_path.exists()


def test_daemon_pid_permission_denied_with_non_syke_process_cleans_stale_pid(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    pid_path.write_text("91919", encoding="utf-8")

    with (
        patch("os.kill", side_effect=PermissionError),
        patch("syke.daemon.daemon._pid_looks_like_syke", return_value=False),
    ):
        running, pid = is_running()

    assert running is False
    assert pid is None
    assert not pid_path.exists()


def test_daemon_pid_reused_by_non_syke_process_cleans_stale_pid(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    pid_path.write_text("91919", encoding="utf-8")

    with (
        patch("os.kill", return_value=None),
        patch("syke.daemon.daemon._pid_looks_like_syke", return_value=False),
    ):
        running, pid = is_running()

    assert running is False
    assert pid is None
    assert not pid_path.exists()


def test_daemon_stale_pid_cleanup_unlink_failure_is_nonfatal(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))

    pid_path.write_text("91919", encoding="utf-8")

    with (
        patch("os.kill", side_effect=ProcessLookupError),
        patch("syke.daemon.daemon._unlink_pidfile", return_value=False) as unlink_pidfile,
    ):
        running, pid = is_running()

    assert running is False
    assert pid is None
    unlink_pidfile.assert_called_once()


def test_pid_identity_requires_daemon_run_signature() -> None:
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["ps"], 0, stdout="/usr/local/bin/syke --user test daemon run --interval 900\n", stderr=""
        ),
    ):
        assert _pid_looks_like_syke(1234) is True

    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["ps"], 0, stdout="/usr/local/bin/syke ask what changed\n", stderr=""
        ),
    ):
        assert _pid_looks_like_syke(1234) is False


def test_pid_safety_accepts_launchd_attestation_when_ps_identity_is_uncertain(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")

    with (
        patch("syke.daemon.daemon._pid_looks_like_syke", return_value=None),
        patch(
            "syke.daemon.daemon.launchd_metadata",
            return_value={"registered": True, "state": "running", "pid": 4242},
        ),
    ):
        assert _pid_is_safe_daemon_target(4242) is True


def test_daemon_process_state_falls_back_to_launchd_when_pidfile_is_missing(monkeypatch, tmp_path):
    pid_path = tmp_path / "syke.pid"
    monkeypatch.setattr("syke.daemon.daemon.PIDFILE", Path(pid_path))
    monkeypatch.setattr("sys.platform", "darwin")

    with (
        patch(
            "syke.daemon.daemon.launchd_metadata",
            return_value={"registered": True, "state": "running", "pid": 4242},
        ),
        patch("os.kill", return_value=None),
    ):
        state = daemon_process_state()

    assert state == {"running": True, "pid": 4242, "source": "launchd"}


def test_daemon_lock_blocks_second_instance(monkeypatch, tmp_path):
    lock_path = tmp_path / "daemon.lock"
    monkeypatch.setattr("syke.daemon.daemon.LOCKFILE", lock_path)

    handle = _acquire_daemon_lock()
    try:
        with pytest.raises(DaemonInstanceLocked):
            _acquire_daemon_lock()
    finally:
        _release_daemon_lock(handle)


# --- Signal handling ---


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_daemon_signal_handler_stops_on_sigterm(sig):
    daemon = SykeDaemon("testuser", interval=900)
    handler = getattr(daemon, "_signal_handler", None)
    if handler is None:
        pytest.skip("Daemon signal handler is not exposed")

    if hasattr(daemon, "running"):
        daemon.running = True
    if hasattr(daemon, "_running"):
        daemon._running = True

    handler(sig, None)

    running = getattr(daemon, "running", None)
    internal_running = getattr(daemon, "_running", None)
    assert (running is False) or (internal_running is False)


# --- Plist generation ---


def test_generate_plist_uses_stable_syke_launcher():
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
    ):
        plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)

    assert "/Users/me/.syke/bin/syke" in plist
    assert "/usr/local/bin/syke" not in plist


# --- TCC protection ---


def test_is_tcc_protected_detects_protected_dirs(monkeypatch):
    home = Path("/Users/testuser")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    assert _is_tcc_protected(Path("/Users/testuser/Documents/project/.venv/bin/syke"))
    assert _is_tcc_protected(Path("/Users/testuser/Desktop/app/bin/syke"))
    assert _is_tcc_protected(Path("/Users/testuser/Downloads/syke"))


def test_is_tcc_protected_allows_safe_paths(monkeypatch):
    home = Path("/Users/testuser")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    assert not _is_tcc_protected(Path("/usr/local/bin/syke"))
    assert not _is_tcc_protected(Path("/Users/testuser/.local/bin/syke"))
    assert not _is_tcc_protected(Path("/opt/homebrew/bin/syke"))
    assert not _is_tcc_protected(Path("/Users/testuser/code/syke/.venv/bin/syke"))


def test_generate_plist_rejects_tcc_path_when_no_alternative():
    with patch(
        "syke.runtime.locator.resolve_background_syke_runtime",
        side_effect=RuntimeError("macOS-protected directory"),
    ):
        with pytest.raises(RuntimeError, match="macOS-protected directory"):
            _call_with_supported_args(generate_plist, user_id="testuser", interval=900)


def test_install_and_start_passes_interval_to_launchd(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")

    with patch("syke.daemon.daemon.install_launchd") as launchd_mock:
        _call_with_supported_args(install_and_start, user_id="testuser", interval=1234)

    launchd_mock.assert_called_once_with("testuser", interval=1234)


def test_install_launchd_writes_interval_to_plist(tmp_path, monkeypatch):
    plist_path = tmp_path / "com.syke.daemon.plist"
    log_path = tmp_path / "daemon.log"
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)
    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", log_path)

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
        patch("subprocess.run", return_value=subprocess.CompletedProcess(["launchctl"], 0)),
    ):
        install_launchd("testuser", interval=1234)

    plist = plist_path.read_text(encoding="utf-8")
    assert "<string>1234</string>" in plist


def test_install_launchd_clears_stale_registration_before_bootstrap(tmp_path, monkeypatch):
    plist_path = tmp_path / "com.syke.daemon.plist"
    log_path = tmp_path / "daemon.log"
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)
    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", log_path)

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        install_launchd("testuser", interval=900)

    assert ["launchctl", "remove", "com.syke.daemon"] in calls
    assert calls[-1] == ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]


def test_install_launchd_falls_back_to_load_when_bootstrap_is_unsupported(tmp_path, monkeypatch):
    plist_path = tmp_path / "com.syke.daemon.plist"
    log_path = tmp_path / "daemon.log"
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["launchctl", "bootstrap", f"gui/{os.getuid()}"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown subcommand")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)
    monkeypatch.setattr("syke.daemon.daemon.LOG_PATH", log_path)

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        install_launchd("testuser", interval=900)

    assert ["launchctl", "load", str(plist_path)] in calls


def test_uninstall_launchd_clears_stale_registration_when_plist_missing(monkeypatch, tmp_path):
    plist_path = tmp_path / "missing.plist"
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["launchctl", "remove", "com.syke.daemon"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)

    with patch("subprocess.run", side_effect=_fake_run):
        removed = uninstall_launchd()

    assert removed is True
    assert ["launchctl", "remove", "com.syke.daemon"] in calls
    assert calls[-1] == ["launchctl", "disable", f"gui/{os.getuid()}/com.syke.daemon"]


def test_launchd_metadata_marks_missing_plist_and_launcher_as_stale(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.syke.daemon.plist"
    launcher_path = tmp_path / "bin" / "syke"
    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)

    with patch(
        "syke.daemon.daemon.launchd_status",
        return_value=f'"Program" = "{launcher_path}"\n"LastExitStatus" = 78\n',
    ):
        metadata = launchd_metadata()

    assert metadata["registered"] is True
    assert metadata["stale"] is True
    assert metadata["last_exit_status"] == 78
    assert metadata["stale_reasons"] == [
        f"plist missing at {plist_path}",
        f"launcher missing at {launcher_path}",
    ]


def test_launchd_status_prefers_service_target_print(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["launchctl", "print"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="state = running\nprogram = /Users/me/.syke/bin/syke\n",
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    with patch("subprocess.run", side_effect=_fake_run):
        status = launchd_status()

    assert status is not None
    assert "state = running" in status
    assert calls[0] == ["launchctl", "print", f"gui/{os.getuid()}/com.syke.daemon"]


def test_launchd_metadata_parses_tabular_launchctl_list_output(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.syke.daemon.plist"
    launcher_path = tmp_path / "bin" / "syke"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")
    plist_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>ProgramArguments</key><array><string>{launcher_path}</string></array></dict></plist>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("syke.daemon.daemon.PLIST_PATH", plist_path)

    with patch(
        "syke.daemon.daemon.launchd_status",
        return_value=f"4242\t0\t{LAUNCHD_LABEL}",
    ):
        metadata = launchd_metadata()

    assert metadata["registered"] is True
    assert metadata["pid"] == 4242
    assert metadata["state"] == "running"
    assert metadata["last_exit_status"] == 0
    assert metadata["program_path"] == str(launcher_path)
    assert metadata["stale"] is False


def test_generate_plist_auto_resolves_safe_alternative():
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/Users/me/.local/bin/syke",),
        target_path=Path("/Users/me/.local/bin/syke"),
    )

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
    ):
        plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)

    assert "/Users/me/.syke/bin/syke" in plist


def test_generate_plist_never_injects_api_key(monkeypatch):
    secret = "sk_test_should_not_appear"
    monkeypatch.setenv("SYKE_API_KEY", secret)
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
    ):
        plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)

    assert secret not in plist


def test_generate_plist_contains_interval_value():
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    with (
        patch("syke.runtime.locator.resolve_background_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
    ):
        plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)
    assert "900" in plist


# --- Cron backend ---


@pytest.mark.parametrize(
    "existing_crontab",
    ["0 * * * * /usr/bin/true\n", None],
)
def test_install_cron_writes_entry(existing_crontab):
    calls = []
    runtime = SykeRuntimeDescriptor(
        mode="external_cli",
        syke_command=("/usr/local/bin/syke",),
        target_path=Path("/usr/local/bin/syke"),
    )

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["crontab", "-l"]:
            if existing_crontab is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no crontab for user")
            return subprocess.CompletedProcess(cmd, 0, stdout=existing_crontab, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with (
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=runtime),
        patch(
            "syke.runtime.locator.ensure_syke_launcher",
            return_value=Path("/Users/me/.syke/bin/syke"),
        ),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        _call_with_supported_args(install_cron, user_id="testuser", interval=900)

    assert any(cmd == ["crontab", "-"] for cmd, _ in calls)
    joined_inputs = "\n".join(str(kwargs.get("input", "")) for _, kwargs in calls)
    assert "syke" in joined_inputs.lower()


@pytest.mark.parametrize("has_entry", [True, False])
def test_uninstall_cron_removes_entry(has_entry):
    existing = (
        "*/15 * * * * /usr/local/bin/syke --user testuser sync >> /tmp/syke.log 2>&1 # syke-daemon\n0 * * * * /usr/bin/true\n"
        if has_entry
        else "0 * * * * /usr/bin/true\n"
    )
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["crontab", "-l"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=existing, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=_fake_run):
        _call_with_supported_args(uninstall_cron, user_id="testuser")

    updates = [kwargs.get("input", "") for cmd, kwargs in calls if cmd == ["crontab", "-"]]
    if has_entry:
        assert updates
        assert all("syke daemon run" not in str(text) for text in updates)
    else:
        assert (not updates) or all("syke daemon run" not in str(text) for text in updates)


@pytest.mark.parametrize(
    "crontab_text,expected",
    [
        ("*/15 * * * * /usr/local/bin/syke --user testuser sync # syke-daemon\n", True),
        ("0 * * * * /usr/bin/true\n", False),
        ("", False),
    ],
)
def test_cron_is_running_states(crontab_text, expected):
    with patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["crontab", "-l"], 0, stdout=crontab_text, stderr=""
        ),
    ):
        actual, _ = _call_with_supported_args(cron_is_running, user_id="testuser")
    assert actual is expected


# --- Platform dispatch ---


@pytest.mark.parametrize(
    "platform_name,expect_cron",
    [("darwin", False), ("linux", True)],
)
def test_install_dispatch(platform_name, expect_cron, monkeypatch):
    monkeypatch.setattr("sys.platform", platform_name)

    with (
        patch("syke.daemon.daemon.install_cron") as cron_mock,
        patch("syke.daemon.daemon.install_launchd", create=True) as launchd_mock,
    ):
        _call_with_supported_args(install_and_start, user_id="testuser", interval=900)

    if expect_cron:
        cron_mock.assert_called_once_with("testuser", interval=900)
        launchd_mock.assert_not_called()
    else:
        launchd_mock.assert_called_once_with("testuser", interval=900)
        cron_mock.assert_not_called()


@pytest.mark.parametrize(
    "platform_name,expect_cron",
    [("darwin", False), ("linux", True)],
)
def test_stop_dispatch(platform_name, expect_cron, monkeypatch):
    monkeypatch.setattr("sys.platform", platform_name)

    with (
        patch("syke.daemon.daemon.is_running", return_value=(False, None)),
        patch("syke.daemon.daemon.uninstall_cron") as cron_mock,
        patch("syke.daemon.daemon.uninstall_launchd") as launchd_mock,
    ):
        _call_with_supported_args(stop_and_unload, user_id="testuser")

    if expect_cron:
        cron_mock.assert_called_once_with()
        launchd_mock.assert_not_called()
    else:
        launchd_mock.assert_called_once_with()
        cron_mock.assert_not_called()


def test_daemon_cycle_ordering():
    daemon = SykeDaemon("testuser", interval=900)
    order: list[str] = []

    with (
        patch.object(daemon, "_health_check", side_effect=lambda: order.append("health") or {}),
        patch.object(daemon, "_heal", side_effect=lambda _health: order.append("heal")),
        patch.object(
            daemon,
            "_synthesize",
            side_effect=lambda _db, _total: (order.append("synthesize"), {"status": "ok"})[1],
        ),
        patch.object(
            daemon, "_distribute", side_effect=lambda _db, _result: order.append("distribute")
        ),
    ):
        daemon._daemon_cycle(MagicMock())

    assert order == ["health", "heal", "synthesize", "distribute"]


def test_stop_and_unload_stops_running_process_before_unloading(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls: list[str] = []

    def _unload() -> None:
        calls.append("unload")

    def _kill(pid: int, sig: int) -> None:
        _ = (pid, sig)
        calls.append("kill")

    with (
        patch(
            "syke.daemon.daemon.is_running",
            side_effect=[(True, 123), (False, None), (False, None)],
        ),
        patch("syke.daemon.daemon.uninstall_launchd", side_effect=_unload),
        patch("syke.daemon.daemon._pid_is_safe_daemon_target", return_value=True),
        patch("os.kill", side_effect=_kill),
        patch("time.monotonic", return_value=0.0),
        patch("time.sleep"),
    ):
        stop_and_unload()

    assert calls == ["unload", "kill"]


def test_stop_and_unload_refuses_to_kill_unverified_pid(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    calls: list[str] = []

    def _unload() -> None:
        calls.append("unload")

    with (
        patch("syke.daemon.daemon.is_running", return_value=(True, 123)),
        patch("syke.daemon.daemon.uninstall_launchd", side_effect=_unload),
        patch("syke.daemon.daemon._pid_is_safe_daemon_target", return_value=False),
        patch("os.kill") as kill_mock,
    ):
        stop_and_unload()

    kill_mock.assert_not_called()
    assert calls == ["unload"]


def test_daemon_run_contains_cycle_failure_and_continues(monkeypatch):
    daemon = SykeDaemon("testuser", interval=1)
    cycle_calls = {"count": 0}

    class _FakeDB:
        db_path = "/tmp/syke.db"

        def initialize(self) -> None:
            return

        def close(self) -> None:
            return

    def _cycle(_db) -> None:
        cycle_calls["count"] += 1
        if cycle_calls["count"] == 1:
            raise RuntimeError("boom")
        daemon.stop()

    monkeypatch.setattr("syke.config.user_syke_db_path", lambda _user: "/tmp/syke.db")
    monkeypatch.setattr("syke.config.user_data_dir", lambda _user: Path("/tmp"))
    monkeypatch.setattr("syke.db.SykeDB", lambda _path: _FakeDB())

    with (
        patch("signal.signal"),
        patch("syke.daemon.daemon._acquire_daemon_lock", return_value=None),
        patch("syke.daemon.daemon._release_daemon_lock"),
        patch("syke.daemon.daemon._write_pid"),
        patch("syke.daemon.daemon._remove_pid"),
        patch.object(daemon, "_start_pi_runtime"),
        patch.object(daemon, "_stop_pi_runtime"),
        patch.object(daemon, "_start_ipc_server"),
        patch.object(daemon, "_stop_ipc_server"),
        patch.object(daemon, "_daemon_cycle", side_effect=_cycle),
    ):
        daemon.run()

    assert cycle_calls["count"] == 2
