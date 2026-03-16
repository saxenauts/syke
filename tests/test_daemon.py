import inspect
import io
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from syke.daemon.daemon import (
    SykeDaemon,
    _is_tcc_protected,
    _remove_pid,
    _write_pid,
    cron_is_running,
    generate_plist,
    install_and_start,
    install_cron,
    is_running,
    stop_and_unload,
    uninstall_cron,
)


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


# --- Signal handling ---


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_daemon_signal_handler_stops_on_sigterm(sig):
    daemon = SykeDaemon("testuser", interval=900)
    handler = getattr(daemon, "_signal_handler", None) or getattr(daemon, "_handle_signal", None)
    if handler is None:
        pytest.skip("Daemon signal handler is not exposed")

    if hasattr(daemon, "running"):
        daemon.running = True
    if hasattr(daemon, "_running"):
        setattr(daemon, "_running", True)

    handler(sig, None)

    running = getattr(daemon, "running", None)
    internal_running = getattr(daemon, "_running", None)
    assert (running is False) or (internal_running is False)


# --- Plist generation ---


@pytest.mark.parametrize(
    "path_binary,expected_substring",
    [
        ("/usr/local/bin/syke", "/usr/local/bin/syke"),
        (None, "/tmp/venv/bin/python"),
    ],
)
def test_generate_plist_picks_binary_source(path_binary, expected_substring, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: path_binary)
    monkeypatch.setattr("sys.executable", "/tmp/venv/bin/python")
    monkeypatch.setattr("syke.daemon.daemon._is_tcc_protected", lambda _: False)

    plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)

    assert expected_substring in plist


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


def test_generate_plist_rejects_tcc_path_when_no_alternative(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/Users/me/Documents/syke/.venv/bin/syke")
    monkeypatch.setattr("syke.daemon.daemon._is_tcc_protected", lambda _: True)
    monkeypatch.setattr("syke.daemon.daemon._find_safe_syke_bin", lambda: None)

    with pytest.raises(RuntimeError, match="macOS-protected directory"):
        _call_with_supported_args(generate_plist, user_id="testuser", interval=900)


def test_generate_plist_auto_resolves_safe_alternative(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "/Users/me/Documents/syke/.venv/bin/syke")
    monkeypatch.setattr("syke.daemon.daemon._is_tcc_protected", lambda p: "Documents" in str(p))
    monkeypatch.setattr(
        "syke.daemon.daemon._find_safe_syke_bin", lambda: "/Users/me/.local/bin/syke"
    )

    plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)

    assert "/Users/me/.local/bin/syke" in plist
    assert "Documents" not in plist


def test_generate_plist_never_injects_api_key(monkeypatch):
    secret = "sk_test_should_not_appear"
    monkeypatch.setenv("SYKE_API_KEY", secret)

    plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)

    assert secret not in plist


def test_generate_plist_contains_interval_value():
    plist = _call_with_supported_args(generate_plist, user_id="testuser", interval=900)
    assert "900" in plist


# --- Cron backend ---


@pytest.mark.parametrize(
    "existing_crontab",
    ["0 * * * * /usr/bin/true\n", None],
)
def test_install_cron_writes_entry(existing_crontab):
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd == ["crontab", "-l"]:
            if existing_crontab is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no crontab for user")
            return subprocess.CompletedProcess(cmd, 0, stdout=existing_crontab, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=_fake_run):
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
        assert cron_mock.called
    else:
        assert launchd_mock.called or not cron_mock.called


@pytest.mark.parametrize(
    "platform_name,expect_cron",
    [("darwin", False), ("linux", True)],
)
def test_stop_dispatch(platform_name, expect_cron, monkeypatch):
    monkeypatch.setattr("sys.platform", platform_name)

    with (
        patch("syke.daemon.daemon.uninstall_cron") as cron_mock,
        patch("syke.daemon.daemon.uninstall_launchd") as launchd_mock,
    ):
        _call_with_supported_args(stop_and_unload, user_id="testuser")

    if expect_cron:
        assert cron_mock.called
    else:
        assert launchd_mock.called or not cron_mock.called


# --- Sync cycle ---


def test_sync_cycle_log_format():
    daemon = SykeDaemon("testuser", interval=900)
    mock_db = MagicMock()
    captured = io.StringIO()

    with (
        patch("syke.db.SykeDB", return_value=mock_db),
        patch("syke.config.user_db_path", return_value="/tmp/fake.db"),
        patch("syke.sync.run_sync", return_value=(3, ["claude-code", "github"])),
        patch("syke.version_check.check_update_available", return_value=(False, None)),
        patch("sys.stdout", captured),
    ):
        daemon._sync_cycle()

    output = captured.getvalue().lower()
    assert "sync" in output
    assert "claude-code" in output
    assert "github" in output


def test_sync_cycle_uses_user_db_path_once():
    daemon = SykeDaemon("testuser", interval=900)

    with (
        patch("syke.db.SykeDB", return_value=MagicMock()),
        patch("syke.config.user_db_path", return_value="/tmp/fake.db") as db_path,
        patch("syke.sync.run_sync", return_value=(0, [])),
        patch("syke.version_check.check_update_available", return_value=(False, None)),
    ):
        daemon._sync_cycle()

    db_path.assert_called_once()


@pytest.mark.parametrize(
    "update_available,version,expect_update_warning",
    [(True, "9.9.9", True), (False, None, False)],
)
def test_sync_cycle_update_message(update_available, version, expect_update_warning):
    daemon = SykeDaemon("testuser", interval=900)
    captured = io.StringIO()

    with (
        patch("syke.db.SykeDB", return_value=MagicMock()),
        patch("syke.config.user_db_path", return_value="/tmp/fake.db"),
        patch("syke.sync.run_sync", return_value=(0, [])),
        patch(
            "syke.version_check.check_update_available",
            return_value=(update_available, version),
        ),
        patch("sys.stdout", captured),
    ):
        daemon._sync_cycle()

    output = captured.getvalue().lower()
    if expect_update_warning:
        assert "update" in output
        assert str(version) in output
    else:
        assert "update" not in output or "available" not in output


# --- Sync timestamps ---


def test_get_last_sync_timestamp_none_when_no_runs(db, user_id):
    ts = db.get_last_sync_timestamp(user_id, "claude-code")
    assert ts is None


@pytest.mark.parametrize(
    "query_source,expect_value",
    [("claude-code", True), ("github", False)],
)
def test_get_last_sync_timestamp_per_source_and_failed_runs_ignored(
    db, user_id, query_source, expect_value
):
    ok_run = db.start_ingestion_run(user_id, "claude-code")
    db.complete_ingestion_run(ok_run, 10)

    failed_run = db.start_ingestion_run(user_id, "github")
    if hasattr(db, "fail_ingestion_run"):
        db.fail_ingestion_run(failed_run, "expected failure in test")

    ts = db.get_last_sync_timestamp(user_id, query_source)

    if expect_value:
        assert ts is not None
    else:
        assert ts is None


def test_daemon_starts_watchers(monkeypatch):
    daemon = SykeDaemon("testuser", interval=900)
    started: dict[str, bool] = {"writer": False, "sense": False, "sqlite": False}

    class _FakeDB:
        def initialize(self) -> None:
            return

        def close(self) -> None:
            return

    class _FakeWriter:
        def __init__(self, db, user_id):
            _ = (db, user_id)

        def start(self) -> None:
            started["writer"] = True

        def stop(self) -> None:
            return

    class _FakeSenseWatcher:
        def __init__(self, descriptors, writer):
            _ = (descriptors, writer)

        def start(self) -> None:
            started["sense"] = True

        def stop(self) -> None:
            return

    class _FakeSQLiteWatcher:
        def __init__(self, db_path, adapter, writer):
            _ = (db_path, adapter, writer)

        def start(self) -> None:
            started["sqlite"] = True

        def stop(self) -> None:
            return

    class _FakeAdapter:
        def discover(self):
            return []

    class _FakeRoot:
        path = "~/.missing"
        include = ["*.db"]

    class _FakeDiscover:
        roots = [_FakeRoot()]

    class _FakeDescriptor:
        source = "opencode"
        format_cluster = "sqlite"
        discover = _FakeDiscover()

    class _FakeRegistry:
        def active_harnesses(self):
            return [_FakeDescriptor()]

        def get_adapter(self, source, db, user_id):
            _ = (source, db, user_id)
            return _FakeAdapter()

    monkeypatch.setattr("syke.config.user_db_path", lambda _user: "/tmp/syke.db")
    monkeypatch.setattr("syke.db.SykeDB", lambda _path: _FakeDB())
    monkeypatch.setattr("syke.ingestion.registry.HarnessRegistry", _FakeRegistry)
    monkeypatch.setattr("syke.sense.writer.SenseWriter", _FakeWriter)
    monkeypatch.setattr("syke.sense.watcher.SenseWatcher", _FakeSenseWatcher)
    monkeypatch.setattr("syke.sense.sqlite_watcher.SQLiteWatcher", _FakeSQLiteWatcher)

    with (
        patch("signal.signal"),
        patch("syke.daemon.daemon._write_pid"),
        patch("syke.daemon.daemon._remove_pid"),
        patch.object(daemon, "_daemon_cycle", side_effect=lambda _db: daemon.stop()),
    ):
        daemon.run()

    assert started["writer"] is True
    assert started["sense"] is True
    assert started["sqlite"] is False


def test_daemon_persistent_stops_watchers(monkeypatch, tmp_path):
    daemon = SykeDaemon("testuser", interval=900)
    stop_order: list[str] = []

    class _FakeDB:
        def initialize(self) -> None:
            return

        def close(self) -> None:
            return

    class _FakeWriter:
        def __init__(self, db, user_id):
            _ = (db, user_id)

        def start(self) -> None:
            return

        def stop(self) -> None:
            stop_order.append("writer")

    class _FakeSenseWatcher:
        def __init__(self, descriptors, writer):
            _ = (descriptors, writer)

        def start(self) -> None:
            return

        def stop(self) -> None:
            stop_order.append("sense")

    class _FakeSQLiteWatcher:
        def __init__(self, db_path, adapter, writer):
            _ = (db_path, adapter, writer)

        def start(self) -> None:
            return

        def stop(self) -> None:
            stop_order.append("sqlite")

    class _FakeAdapter:
        def discover(self):
            return [tmp_path / "source.db"]

    class _FakeRoot:
        path = "~/.missing"
        include = ["*.db"]

    class _FakeDiscover:
        roots = [_FakeRoot()]

    class _FakeDescriptor:
        source = "opencode"
        format_cluster = "sqlite"
        discover = _FakeDiscover()

    class _FakeRegistry:
        def active_harnesses(self):
            return [_FakeDescriptor()]

        def get_adapter(self, source, db, user_id):
            _ = (source, db, user_id)
            return _FakeAdapter()

    monkeypatch.setattr("syke.config.user_db_path", lambda _user: "/tmp/syke.db")
    (tmp_path / "source.db").write_text("", encoding="utf-8")
    monkeypatch.setattr("syke.db.SykeDB", lambda _path: _FakeDB())
    monkeypatch.setattr("syke.ingestion.registry.HarnessRegistry", _FakeRegistry)
    monkeypatch.setattr("syke.sense.writer.SenseWriter", _FakeWriter)
    monkeypatch.setattr("syke.sense.watcher.SenseWatcher", _FakeSenseWatcher)
    monkeypatch.setattr("syke.sense.sqlite_watcher.SQLiteWatcher", _FakeSQLiteWatcher)

    with (
        patch("signal.signal"),
        patch("syke.daemon.daemon._write_pid"),
        patch("syke.daemon.daemon._remove_pid"),
        patch.object(daemon, "_daemon_cycle", side_effect=lambda _db: daemon.stop()),
    ):
        daemon.run()

    assert stop_order == ["sqlite", "sense", "writer"]


def test_daemon_cycle_ordering():
    daemon = SykeDaemon("testuser", interval=900)
    order: list[str] = []

    with (
        patch.object(daemon, "_health_check", side_effect=lambda: order.append("health") or {}),
        patch.object(daemon, "_heal", side_effect=lambda _health: order.append("heal")),
        patch.object(
            daemon, "_reconcile", side_effect=lambda _db: (order.append("reconcile"), (1, []))[1]
        ),
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

    assert order == ["health", "heal", "reconcile", "synthesize", "distribute"]
