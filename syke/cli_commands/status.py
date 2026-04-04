"""Status-style command family for the Syke CLI."""

from __future__ import annotations

import json
from typing import cast

import click

from syke.cli_support.context import get_db
from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.doctor import build_doctor_payload, render_doctor_payload
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import (
    console,
    render_daemon_runtime_summary,
    render_section,
    render_setup_line,
)


def build_status_payload(db, *, user_id: str, cli_provider: str | None) -> dict[str, object]:
    from syke.daemon.ipc import daemon_ipc_status, daemon_runtime_status
    from syke.metrics import runtime_metrics_status
    from syke.observe.trace import self_observation_status

    info = db.get_status(user_id)
    memex = db.get_memex(user_id)
    memory_count = db.count_memories(user_id) if memex else 0
    return {
        "ok": True,
        "user": user_id,
        "initialized": bool(info.get("sources")),
        "provider": provider_payload(cli_provider),
        "daemon": daemon_payload(),
        "daemon_runtime": daemon_runtime_status(user_id),
        "sources": info.get("sources", {}),
        "total_events": info.get("total_events", 0),
        "latest_event_at": info.get("latest_event_at"),
        "recent_runs": info.get("recent_runs", []),
        "memex": {
            "present": bool(memex),
            "created_at": memex.get("created_at") if memex else None,
            "memory_count": memory_count,
        },
        "runtime_signals": {
            "self_observation": self_observation_status(),
            "daemon_ipc": daemon_ipc_status(user_id),
            **runtime_metrics_status(user_id),
        },
    }


@click.command(short_help="Show provider, daemon, source, and memex status.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def status(ctx: click.Context, use_json: bool) -> None:
    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        info = build_status_payload(db, user_id=user_id, cli_provider=ctx.obj.get("provider"))
        if use_json:
            click.echo(json.dumps(info, indent=2))
            return

        console.print(f"\n[bold]syke status[/bold]  [dim]{user_id}[/dim]")
        prov = cast(dict[str, object], info["provider"])
        if prov.get("configured"):
            console.print(
                f"  provider: {prov['id']}  {prov.get('model', '')}  "
                f"[dim]{prov.get('auth_source', '')} · {prov.get('source', '')}[/dim]"
            )
        else:
            error = prov.get("error") or "not configured"
            console.print(f"  [yellow]provider: {error}[/yellow]")
        daemon = cast(dict[str, object], info.get("daemon") or {})
        daemon_runtime = cast(dict[str, object], info.get("daemon_runtime") or {})
        if daemon.get("running") or daemon_runtime.get("reachable"):
            render_daemon_runtime_summary(
                daemon_runtime,
                indent="  ",
                configured_provider=cast(dict[str, object], info["provider"]),
                show_unavailable=True,
            )
        runtime_signals = cast(dict[str, object], info.get("runtime_signals") or {})
        self_observation = cast(dict[str, object], runtime_signals.get("self_observation") or {})
        file_logging = cast(dict[str, object], runtime_signals.get("file_logging") or {})
        metrics_store = cast(dict[str, object], runtime_signals.get("metrics_store") or {})
        daemon_ipc = cast(dict[str, object], runtime_signals.get("daemon_ipc") or {})

        signals: list[tuple[str, bool, str]] = []
        if self_observation.get("enabled") is False:
            signals.append(("self observation", False, str(self_observation.get("detail", ""))))
        if file_logging and not file_logging.get("ok", True):
            signals.append(("file logging", False, str(file_logging.get("detail", ""))))
        if metrics_store and not metrics_store.get("ok", True):
            signals.append(("metrics storage", False, str(metrics_store.get("detail", ""))))
        if daemon_ipc and not daemon_ipc.get("ok", True):
            signals.append(("daemon IPC", False, str(daemon_ipc.get("detail", ""))))

        if signals:
            render_section("Runtime")
            for name, ok, detail in signals:
                icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                suffix = f"  [dim]{detail}[/dim]" if detail else ""
                console.print(f"  {icon} {name}{suffix}")

        if not info["sources"]:
            render_section("Sources")
            render_setup_line("sources", "none yet", detail="run syke setup")
            return

        render_section("Sources")
        for source, count in info["sources"].items():
            console.print(f"  {source}  {count:,} events")
        console.print(f"  [dim]total: {info['total_events']:,}[/dim]")

        if info["recent_runs"]:
            render_section("Recent Ingestion Runs")
            for run in info["recent_runs"][:5]:
                detail = f"{run['events_count']} events • {run['started_at']}"
                render_setup_line(run["source"], run["status"], detail=detail)

        render_section("Memex")
        if info["memex"]["present"]:
            mem_count = info["memex"]["memory_count"]
            created = info["memex"]["created_at"] or "unknown"
            console.print(f"  [green]✓[/green] memex  {mem_count} memories  [dim]{created}[/dim]")
        else:
            console.print("  [dim]✗ memex  not yet built — run syke setup or syke sync[/dim]")
    finally:
        db.close()


@click.command(short_help="Print the current MEMEX.md projection.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="markdown",
    help="Output format",
)
@click.pass_context
def context(ctx: click.Context, use_json: bool, fmt: str) -> None:
    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        if not content:
            console.print("[dim]No memex yet. Run: syke setup[/dim]")
            return
        if use_json or fmt == "json":
            click.echo(json.dumps({"memex": content, "user": user_id}))
        else:
            click.echo(content)
    finally:
        db.close()


@click.command(short_help="Inspect self-observation and memory trends.")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.option("--watch", is_flag=True, help="Live refresh every 30 seconds")
@click.option("--days", "-d", default=7, help="Trend window in days (default: 7)")
@click.pass_context
def observe(ctx: click.Context, use_json: bool, watch: bool, days: int) -> None:
    from syke.health import format_observe, full_observe

    if use_json and watch:
        raise click.UsageError("--json and --watch are mutually exclusive.")

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        if watch:
            import time

            try:
                while True:
                    click.clear()
                    data = full_observe(db, user_id)
                    output = format_observe(data)
                    console.print(output)
                    console.print("\n[dim]Refreshing every 30s — Ctrl+C to stop[/dim]")
                    time.sleep(30)
            except KeyboardInterrupt:
                console.print("\n[dim]Stopped.[/dim]")
        else:
            data = full_observe(db, user_id)
            if use_json:
                click.echo(json.dumps(data, indent=2, default=str))
            else:
                output = format_observe(data)
                console.print(output)
    finally:
        db.close()


@click.command(short_help="Verify auth, runtime, DB, daemon, and memex health.")
@click.option("--network", is_flag=True, help="Test real API connectivity")
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.pass_context
def doctor(ctx: click.Context, network: bool, use_json: bool) -> None:
    payload = build_doctor_payload(ctx, network=network)
    if use_json:
        click.echo(json.dumps(payload, indent=2))
        return
    render_doctor_payload(payload, network=network)


@click.command(short_help="Generate or repair an Observe adapter for a harness path.")
@click.argument("path")
@click.pass_context
def connect(ctx: click.Context, path: str) -> None:
    from syke.config import user_data_dir
    from syke.observe.factory import connect as factory_connect

    user_id = ctx.obj["user"]
    adapters_dir = user_data_dir(user_id) / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)

    success, message = factory_connect(path, llm_fn=None, adapters_dir=adapters_dir)
    if success:
        console.print(f"[green]✓[/green] Connected: {message}")
    else:
        console.print(f"[red]✗[/red] Failed: {message}")
        ctx.exit(1)
