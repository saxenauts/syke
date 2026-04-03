"""Config command family for the Syke CLI."""

from __future__ import annotations

import hashlib
import json
import time
from typing import cast

import click

from syke.cli_support.providers import provider_payload
from syke.cli_support.render import console, render_kv_section


@click.group(invoke_without_command=True)
@click.pass_context
def config(ctx: click.Context) -> None:
    """Manage Syke configuration (~/.syke/config.toml)."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(config_show)


@config.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing config file")
@click.pass_context
def config_init(ctx: click.Context, force: bool) -> None:
    """Generate default config.toml with comments."""
    from syke.config_file import CONFIG_PATH, generate_default_config

    if CONFIG_PATH.exists() and not force:
        console.print(f"[yellow]Config already exists:[/yellow] {CONFIG_PATH}")
        console.print("[dim]Use --force to overwrite.[/dim]")
        return

    user_id = ctx.obj["user"]
    content = generate_default_config(user=user_id)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(content)
    console.print(f"[green]✓[/green] Wrote {CONFIG_PATH}")


@config.command("show")
@click.option("--raw", is_flag=True, help="Show raw TOML file contents")
@click.pass_context
def config_show(ctx: click.Context, raw: bool) -> None:
    """Show effective configuration — what's actually running."""
    from syke import config as c
    from syke.config_file import CONFIG_PATH
    from syke.time import resolve_user_tz

    if raw:
        if CONFIG_PATH.exists():
            console.print(CONFIG_PATH.read_text())
        else:
            console.print(f"[dim]No config file at {CONFIG_PATH}[/dim]")
        return

    console.print("[bold]Syke Configuration[/bold]")
    console.print(
        f"  [dim]File:[/dim] {CONFIG_PATH}"
        + (" [green](loaded)[/green]" if CONFIG_PATH.exists() else " [dim](defaults)[/dim]")
    )
    console.print()

    provider_id, provider_source, provider_details = _resolve_provider_display()
    console.print("  [bold]Provider[/bold]")
    if provider_id:
        console.print(f"    active: [cyan]{provider_id}[/cyan] [dim]({provider_source})[/dim]")
        for key, val in provider_details.items():
            console.print(f"    {key}: [cyan]{val}[/cyan]")
    else:
        console.print(
            "    active: [yellow](none)[/yellow]"
            " — run syke setup or syke auth set <provider> ... --use"
        )
    console.print()

    render_kv_section(
        "Synthesis",
        {
            "thinking level": c.SYNC_THINKING_LEVEL,
            "timeout": f"{c.SYNC_TIMEOUT}s",
            "first run timeout": f"{c.FIRST_RUN_SYNC_TIMEOUT}s",
            "threshold": f"{c.SYNC_EVENT_THRESHOLD} new events",
        },
    )
    render_kv_section("Ask", {"timeout": f"{c.ASK_TIMEOUT}s"})
    render_kv_section(
        "Daemon",
        {"interval": f"{c.DAEMON_INTERVAL}s ({c.DAEMON_INTERVAL // 60} min)"},
    )

    tz = resolve_user_tz()
    tz_display = str(tz) if str(tz) != c.SYKE_TIMEZONE else c.SYKE_TIMEZONE
    if c.SYKE_TIMEZONE == "auto":
        tz_display = f"{tz} (auto)"

    render_kv_section(
        "Identity",
        {
            "user": c.DEFAULT_USER,
            "timezone": tz_display,
            "data": str(c.DATA_DIR),
        },
    )


@config.command("path")
def config_path() -> None:
    """Print config file path."""
    from syke.config_file import CONFIG_PATH

    click.echo(CONFIG_PATH)


@config.command("pi-state-audit", hidden=True)
@click.option("-n", "--lines", default=50, help="Number of lines to show")
@click.option("-f", "--follow", is_flag=True, help="Follow the audit log")
def config_pi_state_audit(lines: int, follow: bool) -> None:
    """Show Syke-side Pi state audit events."""
    from collections import deque

    from syke.pi_state import get_pi_state_audit_path

    path = get_pi_state_audit_path()
    if not path.exists():
        console.print(f"[yellow]No Pi state audit log found at {path}[/yellow]")
        return

    if follow:
        with path.open(encoding="utf-8") as handle:
            handle.seek(0, 2)
            try:
                while True:
                    line = handle.readline()
                    if line:
                        console.print(line.rstrip())
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped.[/dim]")
        return

    with path.open(encoding="utf-8") as handle:
        for line in deque(handle, maxlen=lines):
            console.print(line.rstrip())


@config.command("watch-pi-state", hidden=True)
@click.option("--interval", default=0.5, show_default=True, help="Polling interval in seconds")
def config_watch_pi_state(interval: float) -> None:
    """Watch Pi settings and Syke audit changes during a repro."""
    from syke.pi_state import get_pi_settings_path, get_pi_state_audit_path

    settings_path = get_pi_settings_path()
    audit_path = get_pi_state_audit_path()

    def _fingerprint(path) -> tuple[float | None, str | None]:
        if not path.exists():
            return None, None
        raw = path.read_bytes()
        return path.stat().st_mtime, hashlib.sha256(raw).hexdigest()[:12]

    def _read_text(path) -> str:
        if not path.exists():
            return "(missing)"
        return path.read_text(encoding="utf-8").strip()

    settings_fp = _fingerprint(settings_path)
    audit_fp = _fingerprint(audit_path)
    audit_lines_seen = 0
    if audit_path.exists():
        audit_lines_seen = sum(1 for _ in audit_path.open(encoding="utf-8"))

    console.print(f"[bold]Watching[/bold] {settings_path}")
    console.print(f"[dim]Audit log:[/dim] {audit_path}")
    console.print(f"[dim]Current settings:[/dim] {_read_text(settings_path)}")

    try:
        while True:
            new_settings_fp = _fingerprint(settings_path)
            new_audit_fp = _fingerprint(audit_path)

            if new_audit_fp != audit_fp and audit_path.exists():
                with audit_path.open(encoding="utf-8") as handle:
                    lines = handle.readlines()
                new_lines = lines[audit_lines_seen:]
                for line in new_lines:
                    console.print(f"[cyan]AUDIT[/cyan] {line.rstrip()}")
                audit_lines_seen = len(lines)
                audit_fp = new_audit_fp

            if new_settings_fp != settings_fp:
                payload = {
                    "path": str(settings_path),
                    "fingerprint": new_settings_fp[1],
                    "content": _read_text(settings_path),
                }
                console.print(f"[yellow]SETTINGS CHANGED[/yellow] {json.dumps(payload)}")
                settings_fp = new_settings_fp

            time.sleep(max(interval, 0.1))
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/dim]")


def _resolve_provider_display() -> tuple[str | None, str, dict[str, str]]:
    info = provider_payload(None)
    if not info.get("configured"):
        return None, "", {}

    details = {
        "auth": str(info.get("auth_source") or "missing"),
        "runtime model": str(info.get("model") or "(none)"),
        "endpoint": str(info.get("endpoint") or "(none)"),
        "routing": str(info.get("runtime_provider") or "unknown"),
    }
    return cast(str | None, info.get("id")), str(info.get("source") or "Pi settings"), details
