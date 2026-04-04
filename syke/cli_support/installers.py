"""Installer and managed-runtime helpers for the Syke CLI."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import cast

import click

from syke.cli_support.daemon_state import wait_for_daemon_shutdown, wait_for_daemon_startup
from syke.cli_support.render import console
from syke.config import PROJECT_ROOT, _is_source_install


def detect_install_method() -> str:
    """Detect how syke was installed: 'pipx' | 'pip' | 'uv_tool' | 'uvx' | 'source'."""
    from syke.runtime.locator import resolve_syke_runtime

    if _is_source_install():
        return "source"

    try:
        runtime = resolve_syke_runtime()
        target = runtime.target_path or Path(runtime.syke_command[0])
    except Exception:
        target = None

    target_str = str(target) if target is not None else ""
    if "/uv/tools/" in target_str:
        return "uv_tool"

    try:
        result = subprocess.run(
            ["uv", "tool", "dir"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and target is not None:
            tool_dir = Path(result.stdout.strip()).expanduser()
            resolved_target = target.resolve()
            resolved_tool_dir = tool_dir.resolve()
            if resolved_target == resolved_tool_dir or resolved_tool_dir in resolved_target.parents:
                return "uv_tool"
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["pipx", "list", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and "syke" in result.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if shutil.which("syke") is None:
        return "uvx"
    return "pip"


def resolve_managed_installer(preferred: str) -> str:
    if preferred != "auto":
        if shutil.which(preferred) is None:
            raise click.ClickException(f"{preferred} is not installed or not on PATH.")
        return preferred

    if shutil.which("uv"):
        return "uv"
    if shutil.which("pipx"):
        return "pipx"
    raise click.ClickException(
        "No managed installer found. Install uv or pipx, then retry this command."
    )


def run_managed_checkout_install(
    *,
    user_id: str,
    installer: str,
    restart_daemon: bool,
    prompt: bool,
) -> None:
    from syke.daemon.daemon import install_and_start, is_running, stop_and_unload

    if not _is_source_install():
        raise click.ClickException("This command only works from a source checkout.")

    resolved = resolve_managed_installer(installer)
    if resolved == "uv":
        cmd = ["uv", "tool", "install", "--force", "--reinstall", "--refresh", "--no-cache", "."]
        summary = "non-editable uv tool build for this checkout"
    else:
        cmd = ["pipx", "install", "--force", "."]
        summary = "non-editable pipx install for this checkout"

    if prompt:
        console.print("[bold]Install Current Checkout[/bold]")
        console.print(f"  Checkout:  {PROJECT_ROOT}")
        console.print(f"  Installer: {resolved}")
        console.print(f"  Mode:      {summary}")
        console.print(f"  Command:   {' '.join(cmd)}")
        console.print(
            "  Purpose:   create a launchd-safe managed syke binary for this exact checkout"
        )
        click.confirm("\nContinue?", abort=True)

    was_running, _ = is_running()
    if was_running and restart_daemon:
        console.print("  [dim]Stopping daemon…[/dim]")
        stop_and_unload()
        stop_snapshot = wait_for_daemon_shutdown(user_id)
        if stop_snapshot.get("running") or stop_snapshot.get("registered"):
            raise click.ClickException("Daemon did not stop cleanly before reinstall.")

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        # Show output only on failure so the user can diagnose
        if result.stdout:
            console.print(f"  [dim]{result.stdout.strip()}[/dim]")
        raise click.ClickException("Install failed.")

    console.print("[green]✓[/green] Managed install refreshed.")
    if was_running and restart_daemon:
        console.print("  Restarting daemon...")
        install_and_start(user_id)
        readiness = wait_for_daemon_startup(user_id)
        ipc = cast(dict[str, object], readiness["ipc"])
        if readiness.get("running") and ipc.get("ok"):
            console.print("[green]✓[/green] Daemon restarted.")
            return
        if readiness.get("running"):
            raise click.ClickException(
                f"Daemon process restarted, but warm ask is not ready yet: {ipc.get('detail')}"
            )
        raise click.ClickException("Daemon restart did not become healthy after reinstall.")

    if was_running:
        console.print(
            "[yellow]Daemon still running on the previous process. "
            "Restart it to pick up the new build.[/yellow]"
        )
