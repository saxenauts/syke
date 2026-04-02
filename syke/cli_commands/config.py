"""Config command family for the Syke CLI."""

from __future__ import annotations

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
