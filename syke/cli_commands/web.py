"""Local timeline UI command — prints (and optionally opens) the URL."""

from __future__ import annotations

import webbrowser

import click

from syke.cli_support.render import console
from syke.config import WEB_ENABLED, WEB_PORT
from syke.daemon.web import web_server_status


@click.command("web")
@click.option("--open", "open_browser", is_flag=True, help="Open the URL in your default browser.")
@click.pass_context
def web(ctx: click.Context, open_browser: bool) -> None:
    """Print the URL of the local Syke timeline UI (served by the daemon)."""
    _ = ctx
    if not WEB_ENABLED:
        console.print("[yellow]Web UI disabled[/yellow] (SYKE_WEB_ENABLED=0). Re-enable to use.")
        return
    status = web_server_status(WEB_PORT)
    url = status["url"]
    console.print(f"[bold]Syke timeline:[/bold] {url}")
    if status["reachable"]:
        console.print("  [green]daemon serving[/green]  · localhost only · read-only")
    else:
        console.print(
            "  [yellow]not reachable[/yellow]  · is the daemon running? "
            "Try [cyan]syke daemon start[/cyan]"
        )
    if open_browser and status["reachable"]:
        webbrowser.open(url)
