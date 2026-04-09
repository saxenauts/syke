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
    from syke.trace_store import trace_store_status

    memex = db.get_memex(user_id)
    memory_count = db.count_memories(user_id) if memex else 0
    cycle_count = db.conn.execute(
        "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
        (user_id,),
    ).fetchone()[0]
    return {
        "ok": True,
        "user": user_id,
        "initialized": memory_count > 0 or cycle_count > 0,
        "provider": provider_payload(cli_provider),
        "daemon": daemon_payload(),
        "daemon_runtime": daemon_runtime_status(user_id),
        "cycle_count": cycle_count,
        "memex": {
            "present": bool(memex),
            "created_at": memex.get("created_at") if memex else None,
            "memory_count": memory_count,
        },
        "runtime_signals": {
            "trace_store": trace_store_status(user_id),
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
        trace_store = cast(dict[str, object], runtime_signals.get("trace_store") or {})
        daemon_ipc = cast(dict[str, object], runtime_signals.get("daemon_ipc") or {})

        signals: list[tuple[str, bool, str]] = []
        if trace_store and not trace_store.get("ok", True):
            signals.append(("trace store", False, str(trace_store.get("detail", ""))))
        if daemon_ipc and not daemon_ipc.get("ok", True):
            signals.append(("daemon IPC", False, str(daemon_ipc.get("detail", ""))))

        if signals:
            render_section("Runtime")
            for name, ok, detail in signals:
                icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                suffix = f"  [dim]{detail}[/dim]" if detail else ""
                console.print(f"  {icon} {name}{suffix}")

        if not info["initialized"]:
            render_section("Data")
            render_setup_line("data", "none yet", detail="run syke setup")
            return

        render_section("Data")
        console.print(f"  {info['cycle_count']} cycles")

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


@click.command(short_help="Install adapter markdowns for all known harnesses.")
@click.pass_context
def connect(ctx: click.Context) -> None:
    from syke.observe.bootstrap import ensure_adapters

    from syke.runtime.workspace import WORKSPACE_ROOT

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    results = ensure_adapters(WORKSPACE_ROOT)
    for r in results:
        if r.status == "installed":
            console.print(f"[green]\u2713[/green] {r.source}: installed ({r.detail})")
        elif r.status == "existing":
            console.print(f"[dim]\u2713 {r.source}: already present[/dim]")
        else:
            console.print(f"[yellow]- {r.source}: {r.status} ({r.detail})[/yellow]")
