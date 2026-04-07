"""Maintenance and utility commands for the Syke CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import click
from rich.console import Console
from rich.table import Table

from syke.cli_support.context import get_db
from syke.cli_support.installers import run_managed_checkout_install
from syke.config import _is_source_install

console = Console()


@click.command()
@click.option("--days", "-d", default=None, type=int, help="Limit to last N days")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def cost(ctx: click.Context, days: int | None, use_json: bool) -> None:
    """Show cumulative LLM cost and token usage from metrics.jsonl."""
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]
    tracker = MetricsTracker(user_id)
    runs = tracker._load_all()

    if not runs:
        if use_json:
            click.echo(json.dumps({"total_runs": 0, "total_cost_usd": 0, "runs": []}))
        else:
            console.print("[dim]No metrics recorded yet. Run syke sync or syke ask first.[/dim]")
        return

    if days is not None:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        runs = [run for run in runs if run.get("started_at", "") >= cutoff]
        if not runs:
            if use_json:
                click.echo(json.dumps({"total_runs": 0, "total_cost_usd": 0, "runs": []}))
            else:
                console.print(f"[dim]No metrics in the last {days} day(s).[/dim]")
            return

    total_cost = sum(run.get("cost_usd", 0) for run in runs)
    total_input = sum(run.get("input_tokens", 0) for run in runs)
    total_output = sum(run.get("output_tokens", 0) for run in runs)
    total_thinking = sum(run.get("thinking_tokens", 0) for run in runs)
    total_tokens = total_input + total_output + total_thinking

    by_operation: dict[str, dict[str, int | float]] = {}
    for run in runs:
        operation = run.get("operation", "unknown")
        if operation not in by_operation:
            by_operation[operation] = {"count": 0, "cost_usd": 0.0, "tokens": 0, "errors": 0}
        by_operation[operation]["count"] += 1
        by_operation[operation]["cost_usd"] += run.get("cost_usd", 0)
        by_operation[operation]["tokens"] += (
            run.get("input_tokens", 0) + run.get("output_tokens", 0) + run.get("thinking_tokens", 0)
        )
        if not run.get("success", True):
            by_operation[operation]["errors"] += 1

    if use_json:
        click.echo(
            json.dumps(
                {
                    "total_runs": len(runs),
                    "total_cost_usd": round(total_cost, 6),
                    "total_tokens": total_tokens,
                    "input_tokens": total_input,
                    "output_tokens": total_output,
                    "thinking_tokens": total_thinking,
                    "by_operation": by_operation,
                },
                indent=2,
            )
        )
        return

    period = f"last {days} day(s)" if days else "all time"
    console.print(f"\n[bold]syke cost[/bold]  [dim]{period}[/dim]\n")
    console.print(
        f"  Total:  [bold]${total_cost:.4f}[/bold]  ·  {total_tokens:,} tokens  ·  {len(runs)} runs"
    )
    if total_thinking:
        console.print(
            "  Breakdown:  "
            f"{total_input:,} in  ·  {total_output:,} out  ·  {total_thinking:,} thinking"
        )
    console.print()

    op_table = Table(title="By Operation")
    op_table.add_column("Operation", style="cyan")
    op_table.add_column("Runs", justify="right")
    op_table.add_column("Cost", justify="right", style="green")
    op_table.add_column("Tokens", justify="right")
    op_table.add_column("Errors", justify="right", style="red")

    for operation in sorted(
        by_operation, key=lambda key: by_operation[key]["cost_usd"], reverse=True
    ):
        data = by_operation[operation]
        err_str = str(data["errors"]) if data["errors"] else ""
        op_table.add_row(
            operation,
            str(data["count"]),
            f"${data['cost_usd']:.4f}",
            f"{data['tokens']:,}",
            err_str,
        )

    console.print(op_table)

    recent = runs[-10:]
    if recent:
        console.print("\n[bold]Recent Runs[/bold]")
        for run in reversed(recent):
            ts = run.get("started_at", "")[:19].replace("T", " ")
            operation = run.get("operation", "?")
            usd = run.get("cost_usd", 0)
            tokens = (
                run.get("input_tokens", 0)
                + run.get("output_tokens", 0)
                + run.get("thinking_tokens", 0)
            )
            duration = run.get("duration_seconds", 0)
            ok = "[green]✓[/green]" if run.get("success", True) else "[red]✗[/red]"
            console.print(
                f"  {ts}  {ok}  [cyan]{operation}[/cyan]  "
                f"${usd:.4f}  {tokens:,} tok  {duration:.1f}s"
            )
    console.print()


@click.command(short_help="Run one observe + synthesize cycle.")
@click.option(
    "--source",
    "selected_sources",
    multiple=True,
    hidden=True,
    help="Limit sync to specific sources.",
)
@click.option(
    "--start-daemon-after",
    is_flag=True,
    hidden=True,
    help="Enable background sync after this sync completes.",
)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def sync(
    ctx: click.Context,
    selected_sources: tuple[str, ...],
    start_daemon_after: bool,
    use_json: bool,
) -> None:
    """Sync new data and run synthesis.

    The old copy-pipeline sync has been removed. The agent now reads
    harness data directly via adapter markdowns. This command triggers
    a synthesis cycle through the daemon.
    """
    from syke.llm.backends.pi_synthesis import pi_synthesize

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        if start_daemon_after:
            from syke.daemon.daemon import install_and_start, is_running

            running, _pid = is_running()
            if not running:
                install_and_start(user_id)

        result = pi_synthesize(db, user_id)
        status = result.get("status", "unknown")
        events = int(result.get("events_processed") or 0)

        if use_json:
            click.echo(
                json.dumps(
                    {
                        "ok": status == "completed",
                        "user": user_id,
                        "status": status,
                        "events_processed": events,
                        "memex_updated": result.get("memex_updated"),
                        "error": result.get("error"),
                    },
                    indent=2,
                )
            )
        elif status == "completed":
            console.print(
                f"\n[bold]Synthesis completed.[/bold]  {events} event(s) processed."
            )
        elif status == "skipped":
            console.print("[dim]No new events. Already up to date.[/dim]")
        else:
            console.print(f"[red]Synthesis {status}: {result.get('error', 'unknown')}[/red]")
    finally:
        db.close()


@click.command("install-current")
@click.option(
    "--installer",
    type=click.Choice(["auto", "uv", "pipx"]),
    default="auto",
    show_default=True,
    help="Managed installer to use for this checkout.",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.option(
    "--restart-daemon/--no-restart-daemon",
    default=True,
    show_default=True,
    help="Restart the background daemon after installing if it is running.",
)
@click.pass_context
def install_current(ctx: click.Context, installer: str, yes: bool, restart_daemon: bool) -> None:
    """Install this checkout into a managed tool env for background-safe local use."""
    if not _is_source_install():
        raise click.ClickException("`syke install-current` only works from a source checkout.")

    run_managed_checkout_install(
        user_id=ctx.obj["user"],
        installer=installer,
        restart_daemon=restart_daemon,
        prompt=not yes,
    )
