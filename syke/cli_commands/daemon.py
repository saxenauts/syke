"""Daemon command family for the Syke CLI."""

from __future__ import annotations

import json
from collections import deque
from typing import cast

import click

from syke import __version__
from syke.cli_support import daemon_state
from syke.cli_support.installers import detect_install_method
from syke.cli_support.render import console


@click.group()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Background sync daemon (start, stop, status, logs)."""
    pass


@daemon.command("start")
@click.option(
    "--interval",
    type=int,
    default=900,
    help="Sync interval in seconds (default: 900 = 15 min)",
)
@click.pass_context
def daemon_start(ctx: click.Context, interval: int) -> None:
    from syke.daemon.daemon import install_and_start, is_running

    user_id = ctx.obj["user"]
    running, pid = is_running()
    if running:
        console.print(f"[yellow]Daemon already running (PID {pid})[/yellow]")
        return
    console.print(f"[bold]Starting daemon[/bold] — user: [cyan]{user_id}[/cyan]")
    console.print(f"  Sync interval: {interval}s ({interval // 60} minutes)")
    install_and_start(user_id, interval)
    readiness = daemon_state.wait_for_daemon_startup(user_id)
    ipc = cast(dict[str, object], readiness["ipc"])
    if readiness.get("running") and ipc.get("ok"):
        console.print(f"[green]✓[/green] Daemon started. Sync runs every {interval // 60} minutes.")
    elif readiness.get("running"):
        console.print("[yellow]Daemon process started, but warm ask is not ready yet.[/yellow]")
        console.print(f"  IPC: {ipc.get('detail')}")
    else:
        console.print("[yellow]Daemon registered, but health is not confirmed yet.[/yellow]")
        console.print("  Check status: syke daemon status")
        console.print("  View logs:    syke daemon logs")
        return
    console.print("  Check status: syke daemon status")
    console.print("  View logs:    syke daemon logs")


@daemon.command("stop")
@click.pass_context
def daemon_stop(ctx: click.Context) -> None:
    import sys

    from syke.daemon.daemon import cron_is_running, is_running, launchd_metadata, stop_and_unload

    user_id = ctx.obj["user"]
    running, pid = is_running()
    if sys.platform == "darwin":
        registered = bool(launchd_metadata().get("registered"))
    else:
        registered, _ = cron_is_running()

    if not running and not registered:
        console.print("[dim]Daemon not running[/dim]")
        return

    if running and pid is not None:
        console.print(f"[bold]Stopping daemon[/bold] (PID {pid})")
    else:
        console.print("[bold]Removing daemon registration[/bold]")
    stop_and_unload()
    snapshot = daemon_state.wait_for_daemon_shutdown(user_id)

    if snapshot.get("running") or snapshot.get("registered"):
        detail = f"running={snapshot.get('running')}"
        if snapshot.get("pid") is not None:
            detail += f", pid={snapshot.get('pid')}"
        detail += f", registered={snapshot.get('registered')}"
        console.print(f"[yellow]Daemon stop is incomplete.[/yellow] {detail}")
        return

    console.print("[green]✓[/green] Daemon stopped.")


@daemon.command("status")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def daemon_status_cmd(ctx: click.Context, use_json: bool) -> None:
    from syke.daemon.daemon import LOG_PATH, is_running, launchd_metadata
    from syke.daemon.ipc import daemon_runtime_status
    from syke.daemon.metrics import MetricsTracker
    from syke.runtime.locator import (
        SYKE_BIN,
        describe_runtime_target,
        resolve_background_syke_runtime,
        resolve_syke_runtime,
    )

    running, pid = is_running()
    user_id = ctx.obj["user"]
    launchd = launchd_metadata()
    warm_runtime = daemon_runtime_status(user_id)

    last_run_payload: dict[str, object] | None = None
    try:
        summary = MetricsTracker(user_id).get_summary()
        last = summary.get("last_run")
        if last:
            last_run_payload = {
                "completed_at": last.get("completed_at"),
                "events_processed": last.get("events_processed", 0),
                "success": bool(last.get("success")),
            }
    except Exception:
        last_run_payload = None

    cli_runtime: str | None = None
    cli_runtime_error: str | None = None
    try:
        current_runtime = resolve_syke_runtime()
        cli_runtime = describe_runtime_target(current_runtime)
    except Exception as exc:
        cli_runtime_error = str(exc)

    launcher_target: str | None = None
    launcher_error: str | None = None
    try:
        runtime = resolve_background_syke_runtime()
        launcher_target = describe_runtime_target(runtime)
    except Exception as exc:
        launcher_error = str(exc)

    if use_json:
        click.echo(
            json.dumps(
                {
                    "ok": True,
                    "user": user_id,
                    "running": running,
                    "pid": pid,
                    "launchd": launchd,
                    "log_path": str(LOG_PATH),
                    "cli_runtime": cli_runtime,
                    "cli_runtime_error": cli_runtime_error,
                    "launcher": str(SYKE_BIN),
                    "launcher_target": launcher_target,
                    "launcher_error": launcher_error,
                    "warm_runtime": warm_runtime,
                    "version": __version__,
                    "last_run": last_run_payload,
                },
                indent=2,
            )
        )
        return

    console.print("[bold]Daemon status[/bold]")
    console.print(
        f"  Running:  {'[green]yes[/green] (PID ' + str(pid) + ')' if running else '[red]no[/red]'}"
    )
    if launchd.get("registered") and not running:
        if launchd.get("stale"):
            console.print(
                "  Launchd:  [yellow]stale[/yellow]"
                f" ({'; '.join(cast(list[str], launchd.get('stale_reasons') or []))})"
            )
        else:
            exit_status = launchd.get("last_exit_status")
            if exit_status is None:
                exit_status = "?"
            console.print(f"  Launchd:  registered (last exit: {exit_status})")
    if last_run_payload:
        ts = str(last_run_payload.get("completed_at") or "")[:19].replace("T", " ")
        events = last_run_payload.get("events_processed", 0)
        ok = "[green]ok[/green]" if last_run_payload.get("success") else "[red]failed[/red]"
        console.print(f"  Last run: {ts}  +{events} events  {ok}")
    else:
        try:
            summary = MetricsTracker(user_id).get_summary()
            last = summary.get("last_run")
            if last:
                ts = last.get("completed_at", "")[:19].replace("T", " ")
                events = last.get("events_processed", 0)
                ok = "[green]ok[/green]" if last.get("success") else "[red]failed[/red]"
                console.print(f"  Last run: {ts}  +{events} events  {ok}")
            else:
                console.print("  Last run: [dim]no data yet[/dim]")
        except Exception:
            console.print("  Last run: [dim]unavailable[/dim]")
    console.print(f"  Log:      {LOG_PATH}  [dim](syke daemon logs to view)[/dim]")
    if warm_runtime.get("alive"):
        binding = (
            f"{warm_runtime.get('provider') or '(unknown)'} / "
            f"{warm_runtime.get('model') or '(unknown)'}"
        )
        runtime_pid = warm_runtime.get("runtime_pid")
        detail = f"runtime pid {runtime_pid}" if runtime_pid is not None else None
        if detail:
            console.print(f"  Warm:     {binding}  [dim]({detail})[/dim]")
        else:
            console.print(f"  Warm:     {binding}")
    elif running:
        console.print(
            f"  Warm:     [yellow]unavailable[/yellow] ({warm_runtime.get('detail') or 'unknown'})"
        )
    if cli_runtime is not None:
        console.print(f"  CLI:      {cli_runtime}")
    else:
        console.print(f"  CLI:      [yellow]unavailable: {cli_runtime_error}[/yellow]")
    if launcher_target is not None:
        console.print(f"  Launcher: {SYKE_BIN}")
        console.print(f"  Target:   {launcher_target}")
    else:
        console.print(f"  Launcher: {SYKE_BIN}  [yellow]unavailable: {launcher_error}[/yellow]")

    from syke.version_check import cached_update_available

    update_avail, latest_cached = cached_update_available(__version__)
    console.print(f"  Version:  [cyan]{__version__}[/cyan]", end="")
    if update_avail and latest_cached:
        console.print(
            f"  [yellow]Update available: {latest_cached} — run: syke self-update[/yellow]"
        )
    else:
        console.print()


@daemon.command("run", hidden=True)
@click.option(
    "--interval",
    type=int,
    default=900,
    help="Cycle interval in seconds (default: 900 = 15 min)",
)
@click.pass_context
def daemon_run(ctx: click.Context, interval: int) -> None:
    from syke.daemon.daemon import SykeDaemon

    daemon_instance = SykeDaemon(ctx.obj["user"], interval=interval)
    daemon_instance.run()


@daemon.command()
@click.option("-n", "--lines", default=50, help="Number of lines to show (default: 50)")
@click.option("-f", "--follow", is_flag=True, help="Follow log output (like tail -f)")
@click.option("--errors", is_flag=True, help="Show only ERROR lines")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def logs(ctx: click.Context, lines: int, follow: bool, errors: bool, use_json: bool) -> None:
    import time

    from syke.daemon.daemon import LOG_PATH

    if use_json and follow:
        raise click.UsageError("--json and --follow are mutually exclusive.")

    if not LOG_PATH.exists():
        if use_json:
            click.echo(
                json.dumps(
                    {
                        "ok": False,
                        "path": str(LOG_PATH),
                        "detail": f"No daemon log found at {LOG_PATH}",
                        "lines": [],
                    },
                    indent=2,
                )
            )
        else:
            console.print(f"[yellow]No daemon log found at {LOG_PATH}[/yellow]")
            console.print("[dim]Is the daemon installed? Run: syke daemon start[/dim]")
        return

    if follow:
        with open(LOG_PATH) as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    if not errors or " ERROR " in line:
                        console.print(line.rstrip())
                else:
                    time.sleep(0.2)
    else:
        all_lines = LOG_PATH.read_text().splitlines()
        tail = list(deque(all_lines, maxlen=lines))
        if errors:
            tail = [line for line in tail if " ERROR " in line]
        if use_json:
            click.echo(
                json.dumps(
                    {
                        "ok": True,
                        "path": str(LOG_PATH),
                        "requested_lines": lines,
                        "errors_only": errors,
                        "lines": tail,
                    },
                    indent=2,
                )
            )
        else:
            for line in tail:
                console.print(line)


@click.command("self-update")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def self_update(ctx: click.Context, yes: bool) -> None:
    import subprocess

    from syke import __version__
    from syke.daemon.daemon import install_and_start, is_running, stop_and_unload
    from syke.version_check import check_update_available

    user_id = ctx.obj["user"]
    installed = __version__
    update_available, latest = check_update_available(installed)

    console.print(f"  Installed: [cyan]{installed}[/cyan]")
    if latest:
        console.print(f"  Latest:    [cyan]{latest}[/cyan]")
    else:
        console.print("  [yellow]Could not reach PyPI — check your connection.[/yellow]")
        return
    if not update_available:
        console.print("[green]Already up to date.[/green]")
        return

    method = detect_install_method()

    if method == "uvx":
        console.print(
            "\n[yellow]Installed via uvx — uvx fetches the latest version automatically.[/yellow]"
        )
        console.print("  No action needed: uvx syke ... always uses the latest PyPI release.")
        return
    if method == "source":
        console.print("\n[yellow]Source install detected — update manually:[/yellow]")
        console.print("  git pull && pip install -e .")
        return

    if not yes:
        click.confirm(f"\nUpgrade syke {installed} → {latest}?", abort=True)

    was_running, _ = is_running()
    if was_running:
        console.print("  Stopping daemon...")
        stop_and_unload()
        stop_snapshot = daemon_state.wait_for_daemon_shutdown(user_id)
        if stop_snapshot.get("running") or stop_snapshot.get("registered"):
            console.print("[red]Daemon did not stop cleanly. Aborting update.[/red]")
            return

    if method == "pipx":
        cmd = ["pipx", "upgrade", "syke"]
    elif method == "uv_tool":
        cmd = ["uv", "tool", "upgrade", "syke"]
    else:
        cmd = ["pip", "install", "--upgrade", "syke"]

    console.print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, timeout=300, check=False)
    if result.returncode != 0:
        console.print("[red]Upgrade failed.[/red]")
        return

    if was_running:
        console.print("  Restarting daemon...")
        install_and_start(user_id)
        readiness = daemon_state.wait_for_daemon_startup(user_id)
        ipc = cast(dict[str, object], readiness["ipc"])
        if readiness.get("platform") == "Darwin":
            if readiness.get("running") and ipc.get("ok"):
                console.print(f"[green]✓[/green] syke upgraded to {latest}.")
                return
            if readiness.get("running"):
                console.print(
                    f"[yellow]syke upgraded to {latest}, but warm ask is not ready yet.[/yellow]"
                )
                console.print(f"  IPC: {ipc.get('detail')}")
                return
            console.print(
                "[yellow]syke upgraded to "
                f"{latest}, but daemon restart is not confirmed yet.[/yellow]"
            )
            console.print("  Check status: syke daemon status")
            return

    console.print(f"[green]✓[/green] syke upgraded to {latest}.")
