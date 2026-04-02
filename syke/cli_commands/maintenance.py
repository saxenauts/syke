"""Maintenance and hidden utility commands extracted from the monolithic CLI."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from syke.cli_support.context import get_db, observe_registry

console = Console()


def detect_install_method() -> str:
    from syke.cli import _detect_install_method

    return _detect_install_method()


@click.group(hidden=True)
def ingest() -> None:
    """Ingest data from platforms."""
    pass


@ingest.command("source")
@click.argument("source_name")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_source(ctx: click.Context, source_name: str, yes: bool) -> None:
    """Ingest from a registered source (e.g. claude-code, codex, hermes)."""
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]

    if not yes:
        console.print(
            f"\n[bold yellow]This will ingest data from '{source_name}'[/bold yellow]"
            "\nData stays local — never uploaded.\n"
        )
        if not click.confirm("Proceed with ingestion?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        registry = observe_registry(user_id)
        adapter = registry.get_adapter(source_name, db, user_id)
        if adapter is None:
            console.print(f"[red]No adapter found for '{source_name}'.[/red]")
            console.print("[dim]Use 'syke connect <path>' to generate one.[/dim]")
            return
        with tracker.track(f"ingest_{source_name}") as metrics:
            result = adapter.ingest()
            metrics.events_processed = result.events_count
        console.print(
            f"[green]{source_name} ingestion complete:[/green] {result.events_count} events"
        )
    finally:
        db.close()


@ingest.command("all")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompts for private sources")
@click.pass_context
def ingest_all(ctx: click.Context, yes: bool) -> None:
    """Ingest from all available sources via the registry."""
    console.print("[bold]Ingesting from all sources...[/bold]\n")
    user_id = ctx.obj["user"]
    registry = observe_registry(user_id)
    for desc in registry.active_harnesses():
        try:
            ctx.invoke(ingest_source, source_name=desc.source, yes=yes)
        except (SystemExit, Exception) as e:
            console.print(f"  [yellow]{desc.source} skipped:[/yellow] {e}")
    console.print("\n[bold]All sources processed.[/bold]")


@click.command(hidden=True)
@click.option("--target", "-t", required=True, type=click.Path(), help="Target directory")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["memex-md", "user-md"]),
    default="memex-md",
)
@click.pass_context
def inject(ctx: click.Context, target: str, fmt: str) -> None:
    """Inject memex into a target directory."""
    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        filename = "MEMEX.md" if fmt == "memex-md" else "USER.md"
        target_path = Path(target) / filename
        target_path.write_text(content)
        console.print(f"[green]Memex injected to {target_path}[/green]")
    finally:
        db.close()


@click.command()
@click.pass_context
def sync(ctx: click.Context) -> None:
    """Sync new data and run synthesis."""
    from syke.sync import run_sync

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        sources = db.get_sources(user_id)
        if not sources:
            console.print("[yellow]No data yet. Run: syke setup[/yellow]")
            return

        console.print(f"\n[bold]Syncing[/bold] — user: [cyan]{user_id}[/cyan]")
        console.print(f"  Sources: {', '.join(sources)}\n")

        total_new, synced = run_sync(db, user_id, out=console)

        console.print(
            f"\n[bold]Synced {total_new} new event(s) from {len(sources)} source(s).[/bold]"
        )
        if total_new == 0:
            console.print("[dim]Already up to date.[/dim]")
    finally:
        db.close()
