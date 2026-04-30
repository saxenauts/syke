from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import click
import pytest

from syke.cli_support import installers
from syke.config import PROJECT_ROOT


def _result(*, returncode: int, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def test_detect_install_method_source_short_circuits_runtime_detection() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.runtime.locator.resolve_syke_runtime") as resolve_runtime,
    ):
        assert installers.detect_install_method() == "source"

    resolve_runtime.assert_not_called()


def test_detect_install_method_uv_tool_from_path_marker_uses_syke_command_fallback() -> None:
    runtime = SimpleNamespace(target_path=None, syke_command=["/tmp/uv/tools/syke/bin/syke"])

    with (
        patch("syke.cli_support.installers._is_source_install", return_value=False),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=runtime),
        patch("syke.cli_support.installers.subprocess.run") as run_mock,
    ):
        assert installers.detect_install_method() == "uv_tool"

    run_mock.assert_not_called()


def test_detect_install_method_uv_tool_from_uv_tool_dir_parent_match() -> None:
    runtime = SimpleNamespace(target_path=Path("/tmp/syke-managed/bin/syke"), syke_command=["syke"])

    with (
        patch("syke.cli_support.installers._is_source_install", return_value=False),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=runtime),
        patch(
            "syke.cli_support.installers.subprocess.run",
            return_value=_result(returncode=0, stdout="/tmp/syke-managed"),
        ) as run_mock,
    ):
        assert installers.detect_install_method() == "uv_tool"

    run_mock.assert_called_once_with(
        ["uv", "tool", "dir"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def test_detect_install_method_returns_pipx_when_pipx_has_syke() -> None:
    runtime = SimpleNamespace(target_path=Path("/usr/local/bin/syke"), syke_command=["syke"])

    with (
        patch("syke.cli_support.installers._is_source_install", return_value=False),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=runtime),
        patch(
            "syke.cli_support.installers.subprocess.run",
            side_effect=[
                _result(returncode=1),
                _result(returncode=0, stdout="syke\n"),
            ],
        ) as run_mock,
    ):
        assert installers.detect_install_method() == "pipx"

    assert [args.args[0] for args in run_mock.call_args_list] == [
        ["uv", "tool", "dir"],
        ["pipx", "list", "--short"],
    ]


def test_detect_install_method_returns_uvx_when_syke_binary_is_missing() -> None:
    runtime = SimpleNamespace(target_path=Path("/usr/local/bin/syke"), syke_command=["syke"])

    with (
        patch("syke.cli_support.installers._is_source_install", return_value=False),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=runtime),
        patch(
            "syke.cli_support.installers.subprocess.run",
            side_effect=[
                _result(returncode=1),
                _result(returncode=1),
            ],
        ),
        patch("syke.cli_support.installers.shutil.which", return_value=None),
    ):
        assert installers.detect_install_method() == "uvx"


def test_detect_install_method_returns_pip_when_syke_binary_exists() -> None:
    runtime = SimpleNamespace(target_path=Path("/usr/local/bin/syke"), syke_command=["syke"])

    with (
        patch("syke.cli_support.installers._is_source_install", return_value=False),
        patch("syke.runtime.locator.resolve_syke_runtime", return_value=runtime),
        patch(
            "syke.cli_support.installers.subprocess.run",
            side_effect=[
                _result(returncode=1),
                _result(returncode=1),
            ],
        ),
        patch("syke.cli_support.installers.shutil.which", return_value="/usr/bin/syke"),
    ):
        assert installers.detect_install_method() == "pip"


def test_resolve_managed_installer_manual_success() -> None:
    with patch(
        "syke.cli_support.installers.shutil.which", return_value="/usr/local/bin/pipx"
    ) as which:
        assert installers.resolve_managed_installer("pipx") == "pipx"

    which.assert_called_once_with("pipx")


def test_resolve_managed_installer_manual_missing_raises() -> None:
    with patch("syke.cli_support.installers.shutil.which", return_value=None):
        with pytest.raises(click.ClickException, match="pipx is not installed or not on PATH."):
            installers.resolve_managed_installer("pipx")


def test_resolve_managed_installer_auto_prefers_uv_over_pipx() -> None:
    with patch(
        "syke.cli_support.installers.shutil.which",
        side_effect=lambda name: "/usr/local/bin/uv" if name == "uv" else "/usr/local/bin/pipx",
    ) as which:
        assert installers.resolve_managed_installer("auto") == "uv"

    assert which.call_args_list == [call("uv")]


def test_resolve_managed_installer_auto_uses_pipx_when_uv_missing() -> None:
    with patch(
        "syke.cli_support.installers.shutil.which",
        side_effect=lambda name: None if name == "uv" else "/usr/local/bin/pipx",
    ) as which:
        assert installers.resolve_managed_installer("auto") == "pipx"

    assert which.call_args_list == [call("uv"), call("pipx")]


def test_resolve_managed_installer_auto_raises_when_no_installer_available() -> None:
    with patch("syke.cli_support.installers.shutil.which", return_value=None):
        with pytest.raises(
            click.ClickException,
            match="No managed installer found. Install uv or pipx, then retry this command.",
        ):
            installers.resolve_managed_installer("auto")


def test_run_managed_checkout_install_requires_source_checkout() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=False),
        patch("syke.cli_support.installers.resolve_managed_installer") as resolve_installer,
    ):
        with pytest.raises(click.ClickException, match="only works from a source checkout"):
            installers.run_managed_checkout_install(
                user_id="test-user",
                installer="auto",
                restart_daemon=True,
                prompt=False,
            )

    resolve_installer.assert_not_called()


def test_run_managed_checkout_install_aborts_when_daemon_stop_is_unclean() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 99})),
        patch("syke.daemon.daemon.stop_and_unload") as stop_and_unload,
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": True, "registered": False},
        ),
        patch("syke.cli_support.installers.subprocess.run") as run_mock,
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
    ):
        with pytest.raises(click.ClickException, match="Daemon did not stop cleanly"):
            installers.run_managed_checkout_install(
                user_id="test-user",
                installer="auto",
                restart_daemon=True,
                prompt=False,
            )

    stop_and_unload.assert_called_once()
    run_mock.assert_not_called()
    install_and_start.assert_not_called()


def test_run_managed_checkout_install_fails_install_without_attempting_restart() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 99})),
        patch("syke.daemon.daemon.stop_and_unload") as stop_and_unload,
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch(
            "syke.cli_support.installers.subprocess.run",
            return_value=_result(returncode=1, stdout="build failed"),
        ) as run_mock,
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
        patch("syke.cli_support.installers.wait_for_daemon_startup") as wait_startup,
    ):
        with pytest.raises(click.ClickException, match="Install failed."):
            installers.run_managed_checkout_install(
                user_id="test-user",
                installer="auto",
                restart_daemon=True,
                prompt=False,
            )

    stop_and_unload.assert_called_once()
    install_and_start.assert_not_called()
    wait_startup.assert_not_called()
    run_mock.assert_called_once_with(
        ["uv", "tool", "install", "--force", "--reinstall", "--refresh", "--no-cache", "."],
        cwd=str(PROJECT_ROOT),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_run_managed_checkout_install_restarts_daemon_after_successful_reinstall() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="pipx"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 99})),
        patch("syke.daemon.daemon.stop_and_unload") as stop_and_unload,
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch(
            "syke.cli_support.installers.subprocess.run", return_value=_result(returncode=0)
        ) as run_mock,
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
        patch(
            "syke.cli_support.installers.wait_for_daemon_startup",
            return_value={"running": True, "ipc": {"ok": True, "detail": "ready"}},
        ) as wait_startup,
    ):
        installers.run_managed_checkout_install(
            user_id="test-user",
            installer="auto",
            restart_daemon=True,
            prompt=False,
        )

    stop_and_unload.assert_called_once()
    install_and_start.assert_called_once_with("test-user")
    wait_startup.assert_called_once_with("test-user")
    run_mock.assert_called_once_with(
        ["pipx", "install", "--force", "."],
        cwd=str(PROJECT_ROOT),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_run_managed_checkout_install_restart_reports_warm_ask_not_ready() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 99})),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch("syke.cli_support.installers.subprocess.run", return_value=_result(returncode=0)),
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
        patch(
            "syke.cli_support.installers.wait_for_daemon_startup",
            return_value={"running": True, "ipc": {"ok": False, "detail": "socket missing"}},
        ),
    ):
        with pytest.raises(
            click.ClickException,
            match="Daemon process restarted, but warm ask is not ready yet: socket missing",
        ):
            installers.run_managed_checkout_install(
                user_id="test-user",
                installer="auto",
                restart_daemon=True,
                prompt=False,
            )

    install_and_start.assert_called_once_with("test-user")


def test_run_managed_checkout_install_restart_reports_unhealthy_daemon_process() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 99})),
        patch("syke.daemon.daemon.stop_and_unload"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_shutdown",
            return_value={"running": False, "registered": False},
        ),
        patch("syke.cli_support.installers.subprocess.run", return_value=_result(returncode=0)),
        patch("syke.daemon.daemon.install_and_start"),
        patch(
            "syke.cli_support.installers.wait_for_daemon_startup",
            return_value={"running": False, "ipc": {"ok": False, "detail": "down"}},
        ),
    ):
        with pytest.raises(
            click.ClickException,
            match="Daemon restart did not become healthy after reinstall.",
        ):
            installers.run_managed_checkout_install(
                user_id="test-user",
                installer="auto",
                restart_daemon=True,
                prompt=False,
            )


def test_run_managed_checkout_install_keeps_existing_daemon_when_restart_disabled() -> None:
    with (
        patch("syke.cli_support.installers._is_source_install", return_value=True),
        patch("syke.cli_support.installers.resolve_managed_installer", return_value="uv"),
        patch("syke.daemon.daemon.is_running", return_value=(True, {"pid": 99})),
        patch("syke.daemon.daemon.stop_and_unload") as stop_and_unload,
        patch("syke.cli_support.installers.wait_for_daemon_shutdown") as wait_shutdown,
        patch("syke.cli_support.installers.subprocess.run", return_value=_result(returncode=0)),
        patch("syke.daemon.daemon.install_and_start") as install_and_start,
        patch("syke.cli_support.installers.wait_for_daemon_startup") as wait_startup,
        patch("syke.cli_support.installers.console.print") as print_mock,
    ):
        installers.run_managed_checkout_install(
            user_id="test-user",
            installer="auto",
            restart_daemon=False,
            prompt=False,
        )

    stop_and_unload.assert_not_called()
    wait_shutdown.assert_not_called()
    install_and_start.assert_not_called()
    wait_startup.assert_not_called()
    assert any("previous process" in args.args[0] for args in print_mock.call_args_list)
