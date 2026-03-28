"""Click CLI for Syke."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

import click
from rich.console import Console
from rich.table import Table

from syke import __version__
from syke.config import (
    DEFAULT_USER,
    _is_source_install,
    PROJECT_ROOT,
    user_events_db_path,
    user_syke_db_path,
)
from syke.db import SykeDB
from syke.time import format_for_human

console = Console()


def get_db(user_id: str) -> SykeDB:
    """Get an initialized DB for a user."""
    return SykeDB(user_syke_db_path(user_id), event_db_path=user_events_db_path(user_id))


@click.group(invoke_without_command=True)
@click.option("--user", "-u", default=DEFAULT_USER, help="User ID")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.option("--provider", "-p", default=None, help="Override LLM provider for this invocation")
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, user: str, verbose: bool, provider: str | None) -> None:
    """Syke — Personal context daemon."""
    ctx.ensure_object(dict)
    ctx.obj["user"] = user
    ctx.obj["verbose"] = verbose
    ctx.obj["provider"] = provider

    if provider:
        os.environ["SYKE_PROVIDER"] = provider

    from syke.metrics import setup_logging

    setup_logging(user, verbose=verbose)

    if ctx.invoked_subcommand is None:
        _show_dashboard(ctx.obj["user"])


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show status of ingested data."""
    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        info = db.get_status(user_id)

        console.print(f"\n[bold]Syke Status[/bold] — user: [cyan]{user_id}[/cyan]\n")

        if not info["sources"]:
            console.print("[dim]No data yet. Run: syke setup --user <name>[/dim]")
            return

        table = Table(title="Event Sources")
        table.add_column("Source", style="cyan")
        table.add_column("Events", justify="right", style="green")

        for source, count in info["sources"].items():
            table.add_row(source, str(count))
        table.add_row("[bold]Total[/bold]", f"[bold]{info['total_events']}[/bold]")
        console.print(table)

        if info["recent_runs"]:
            console.print("\n[bold]Recent Ingestion Runs[/bold]")
            for run in info["recent_runs"][:5]:
                status_color = "green" if run["status"] == "completed" else "red"
                console.print(
                    f"  [{status_color}]{run['status']}[/{status_color}] "
                    f"{run['source']} — {run['events_count']} events "
                    f"({run['started_at']})"
                )

        # Show memex stats
        memex = db.get_memex(user_id)
        if memex:
            mem_count = db.count_memories(user_id)
            created = memex.get("created_at", "unknown")
            console.print(f"\n[bold]Memex[/bold]: synthesized at {created} ({mem_count} memories)")
        else:
            console.print("\n[dim]No memex yet. Run: syke setup --user <name>[/dim]")
    finally:
        db.close()


@cli.command()
@click.option("--days", "-d", default=None, type=int, help="Limit to last N days")
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Output as JSON",
)
@click.pass_context
def cost(ctx: click.Context, days: int | None, use_json: bool) -> None:
    """Show cumulative LLM cost and token usage from metrics.jsonl."""
    from datetime import UTC, datetime, timedelta

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

    # Filter by date if --days specified
    if days is not None:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        runs = [r for r in runs if r.get("started_at", "") >= cutoff]
        if not runs:
            if use_json:
                click.echo(json.dumps({"total_runs": 0, "total_cost_usd": 0, "runs": []}))
            else:
                console.print(f"[dim]No metrics in the last {days} day(s).[/dim]")
            return

    # Aggregate
    total_cost = sum(r.get("cost_usd", 0) for r in runs)
    total_input = sum(r.get("input_tokens", 0) for r in runs)
    total_output = sum(r.get("output_tokens", 0) for r in runs)
    total_thinking = sum(r.get("thinking_tokens", 0) for r in runs)
    total_tokens = total_input + total_output + total_thinking

    by_op: dict[str, dict[str, int | float]] = {}
    for r in runs:
        op = r.get("operation", "unknown")
        if op not in by_op:
            by_op[op] = {"count": 0, "cost_usd": 0.0, "tokens": 0, "errors": 0}
        by_op[op]["count"] += 1
        by_op[op]["cost_usd"] += r.get("cost_usd", 0)
        by_op[op]["tokens"] += (
            r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
        )
        if not r.get("success", True):
            by_op[op]["errors"] += 1

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
                    "by_operation": by_op,
                },
                indent=2,
            )
        )
        return

    # Header
    period = f"last {days} day(s)" if days else "all time"
    console.print(f"\n[bold]Syke Cost[/bold] — {period}\n")
    console.print(
        f"  Total:  [bold]${total_cost:.4f}[/bold]  ·  {total_tokens:,} tokens  ·  {len(runs)} runs"
    )
    if total_thinking:
        console.print(
            f"  Breakdown:  {total_input:,} in  ·  {total_output:,} out  ·  {total_thinking:,} thinking"
        )
    console.print()

    # By-operation table
    op_table = Table(title="By Operation")
    op_table.add_column("Operation", style="cyan")
    op_table.add_column("Runs", justify="right")
    op_table.add_column("Cost", justify="right", style="green")
    op_table.add_column("Tokens", justify="right")
    op_table.add_column("Errors", justify="right", style="red")

    for op in sorted(by_op, key=lambda k: by_op[k]["cost_usd"], reverse=True):
        d = by_op[op]
        err_str = str(d["errors"]) if d["errors"] else ""
        op_table.add_row(op, str(d["count"]), f"${d['cost_usd']:.4f}", f"{d['tokens']:,}", err_str)

    console.print(op_table)

    # Recent runs (last 10)
    recent = runs[-10:]
    if recent:
        console.print("\n[bold]Recent Runs[/bold]")
        for r in reversed(recent):
            ts = r.get("started_at", "")[:19].replace("T", " ")
            op = r.get("operation", "?")
            usd = r.get("cost_usd", 0)
            tok = r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("thinking_tokens", 0)
            dur = r.get("duration_seconds", 0)
            ok = "[green]ok[/green]" if r.get("success", True) else "[red]fail[/red]"
            console.print(f"  {ts}  {ok}  [cyan]{op}[/cyan]  ${usd:.4f}  {tok:,} tok  {dur:.1f}s")
    console.print()


@cli.group(hidden=True)
def ingest() -> None:
    """Ingest data from platforms."""
    pass


@ingest.command("source")
@click.argument("source_name")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_source(ctx: click.Context, source_name: str, yes: bool) -> None:
    """Ingest from a registered source (e.g. claude-code, codex, hermes)."""
    from syke.observe.registry import HarnessRegistry
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
        registry = HarnessRegistry()
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


@ingest.command("chatgpt")
@click.option("--file", "-f", "file_path", required=True, type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_chatgpt(ctx: click.Context, file_path: str, yes: bool) -> None:
    """Ingest ChatGPT export ZIP file."""
    from syke.observe.importers import ChatGPTAdapter
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]

    if not yes:
        console.print(
            f"\n[bold yellow]This will read your ChatGPT export[/bold yellow]"
            f"\nfrom [cyan]{file_path}[/cyan]"
            "\n\nThis includes all your ChatGPT conversations."
            "\nData stays local — never uploaded.\n"
        )
        if not click.confirm("Proceed with ingestion?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        with tracker.track("ingest_chatgpt", file=file_path) as metrics:
            adapter = ChatGPTAdapter(db, user_id)
            result = adapter.ingest(file_path=file_path)
            metrics.events_processed = result.events_count
        console.print(f"[green]ChatGPT ingestion complete:[/green] {result.events_count} events")
    finally:
        db.close()


@ingest.command("all")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompts for private sources")
@click.pass_context
def ingest_all(ctx: click.Context, yes: bool) -> None:
    """Ingest from all available sources via the registry."""
    from syke.observe.registry import HarnessRegistry

    console.print("[bold]Ingesting from all sources...[/bold]\n")
    user_id = ctx.obj["user"]
    registry = HarnessRegistry()
    for desc in registry.active_harnesses():
        try:
            ctx.invoke(ingest_source, source_name=desc.source, yes=yes)
        except (SystemExit, Exception) as e:
            console.print(f"  [yellow]{desc.source} skipped:[/yellow] {e}")
    console.print("\n[bold]All sources processed.[/bold]")

def _detect_install_method() -> str:
    """Detect how syke was installed: 'pipx' | 'pip' | 'uvx' | 'source'."""
    import shutil
    import subprocess

    if _is_source_install():
        return "source"
    try:
        r = subprocess.run(
            ["pipx", "list", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and "syke" in r.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if shutil.which("syke") is None:
        return "uvx"
    return "pip"


def _resolve_managed_installer(preferred: str) -> str:
    import shutil

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


def _run_managed_checkout_install(
    *,
    user_id: str,
    installer: str,
    restart_daemon: bool,
    prompt: bool,
) -> None:
    import subprocess

    from syke.daemon.daemon import install_and_start, is_running, stop_and_unload

    if not _is_source_install():
        raise click.ClickException("This command only works from a source checkout.")

    resolved = _resolve_managed_installer(installer)
    if resolved == "uv":
        cmd = ["uv", "tool", "install", "--force", "--reinstall", "--refresh", "--no-cache", "."]
        summary = "non-editable uv tool build for this checkout"
    else:
        cmd = ["pipx", "install", "--force", "."]
        summary = "non-editable pipx install for this checkout"

    console.print("[bold]Install Current Checkout[/bold]")
    console.print(f"  Checkout:  {PROJECT_ROOT}")
    console.print(f"  Installer: {resolved}")
    console.print(f"  Mode:      {summary}")
    console.print(f"  Command:   {' '.join(cmd)}")
    console.print(
        "  Purpose:   create a launchd-safe managed syke binary for this exact checkout"
    )

    if prompt:
        click.confirm("\nContinue?", abort=True)

    was_running, _ = is_running()
    if was_running and restart_daemon:
        console.print("  Stopping daemon...")
        stop_and_unload()

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        raise click.ClickException("Install failed.")

    console.print("[green]✓[/green] Managed install refreshed.")
    if was_running and restart_daemon:
        console.print("  Restarting daemon...")
        install_and_start(user_id)
        console.print("[green]✓[/green] Daemon restarted.")
    elif was_running:
        console.print(
            "[yellow]Daemon still running on the previous process. Restart it to pick up the new build.[/yellow]"
        )


@cli.command("install-current")
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

    _run_managed_checkout_install(
        user_id=ctx.obj["user"],
        installer=installer,
        restart_daemon=restart_daemon,
        prompt=not yes,
    )


@cli.command(hidden=True)
@click.option("--target", "-t", required=True, type=click.Path(), help="Target directory")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["claude-md", "user-md"]),
    default="claude-md",
)
@click.pass_context
def inject(ctx: click.Context, target: str, fmt: str) -> None:
    """Inject memex into a target directory."""
    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        filename = "CLAUDE.md" if fmt == "claude-md" else "USER.md"
        target_path = Path(target) / filename
        target_path.write_text(content)
        console.print(f"[green]Memex injected to {target_path}[/green]")
    finally:
        db.close()


@cli.command(hidden=True)
@click.option("--since", default=None, help="ISO date to filter from")
@click.option("--limit", "-n", default=50, help="Max events to show")
@click.option("--source", "-s", default=None, help="Filter by source")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["table", "json"]),
    default="table",
    help="Output format",
)
@click.pass_context
def timeline(
    ctx: click.Context, since: str | None, limit: int, source: str | None, fmt: str
) -> None:
    """Show the event timeline."""
    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        events = db.get_events(user_id, source=source, since=since, limit=limit)
        if not events:
            if fmt == "json":
                click.echo("[]")
            else:
                console.print("[dim]No events found.[/dim]")
            return

        if fmt == "json":
            click.echo(json.dumps(events, indent=2, default=str))
            return

        def _fmt_time(ts: str) -> str:
            try:
                display = format_for_human(ts)
                if display.startswith("today "):
                    return f"[bold]today[/bold] {display.removeprefix('today ')}"
                if display.startswith("yesterday "):
                    return f"[dim]yesterday[/dim] {display.removeprefix('yesterday ')}"
                return display
            except Exception:
                return ts[:19]

        def _clean_title(title: str) -> str:
            """Strip noisy prefixes from titles."""
            t = (title or "").strip()
            for prefix in ("[CONTEXT]: ", "[CONTEXT]:", "CONTEXT: "):
                if t.startswith(prefix):
                    t = t[len(prefix) :]
            return t

        _SOURCE_COLORS = {
            "claude-code": "cyan",
            "chatgpt": "yellow",
            "codex": "green",
            "hermes": "magenta",
            "opencode": "blue",
        }

        _TYPE_COLORS = {
            "session": "cyan",
            "push": "green",
            "readme": "dim green",
            "observation": "yellow",
            "conversation": "yellow",
            "email": "blue",
            "task": "magenta",
        }

        table = Table(title=f"Timeline — {user_id}", show_lines=False, pad_edge=True)
        table.add_column("Time", style="dim", min_width=22, no_wrap=True)
        table.add_column("Source", min_width=12, no_wrap=True)
        table.add_column("Type", min_width=12, no_wrap=True)
        table.add_column("Title", ratio=1, no_wrap=True)

        for ev in events:
            src = ev["source"]
            etype = ev["event_type"]
            src_color = _SOURCE_COLORS.get(src, "white")
            type_color = _TYPE_COLORS.get(etype, "white")
            table.add_row(
                _fmt_time(ev["timestamp"]),
                f"[{src_color}]{src}[/{src_color}]",
                f"[{type_color}]{etype}[/{type_color}]",
                _clean_title(ev.get("title") or ""),
            )

        console.print(table)
    finally:
        db.close()


@cli.command(hidden=True)
@click.argument("query")
@click.option("--limit", "-n", default=5, help="Max results to show")
@click.option("--source", "-s", default=None, help="Filter by source")
@click.pass_context
def show(ctx: click.Context, query: str, limit: int, source: str | None) -> None:
    """Search events and display full content."""
    from rich.panel import Panel

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        # Fetch extra to account for source filtering in Python
        results = db.search_events(user_id, query, limit=limit * 3)
        if source:
            results = [r for r in results if r["source"] == source]
        results = results[:limit]

        if not results:
            console.print(f"[dim]No events matching '{query}'.[/dim]")
            return

        console.print(f"\n[bold]Search: '{query}'[/bold] — {len(results)} result(s)\n")

        for ev in results:
            subtitle = f"{ev['source']} | {ev['event_type']} | {ev['timestamp'][:19]}"
            content = (ev.get("content") or "")[:2000]

            # Append metadata summary if available
            meta = ev.get("metadata")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = None
            if isinstance(meta, dict):
                meta_parts = [
                    f"{k}={v}" for k, v in list(meta.items())[:8] if v not in (None, "", [])
                ]
                if meta_parts:
                    content += f"\n\n[dim]{'  '.join(meta_parts)}[/dim]"

            console.print(
                Panel(
                    content,
                    title=ev.get("title") or "(untitled)",
                    subtitle=subtitle,
                    expand=True,
                )
            )
    finally:
        db.close()


@cli.command()
@click.argument("question")
@click.pass_context
def ask(ctx: click.Context, question: str) -> None:
    """Ask a natural language question about the user."""
    import logging as _logging
    import signal as _signal
    import sys as _sys

    from syke.llm.backends import AskEvent
    from syke.llm.pi_runtime import run_ask
    from syke.llm.env import resolve_provider

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
        provider_label = provider.id
    except Exception:
        provider_label = "unknown"

    _sigterm_fired = False

    def _on_sigterm(signum, frame):
        nonlocal _sigterm_fired
        _sigterm_fired = True
        raise SystemExit(143)

    prev_handler = _signal.signal(_signal.SIGTERM, _on_sigterm)

    try:
        syke_logger = _logging.getLogger("syke")
        saved_levels = {
            h: h.level
            for h in syke_logger.handlers
            if isinstance(h, _logging.StreamHandler) and not isinstance(h, _logging.FileHandler)
        }
        for h in saved_levels:
            h.setLevel(_logging.CRITICAL)

        has_thinking = False
        has_streamed_text = False

        def _on_event(event: AskEvent) -> None:
            nonlocal has_thinking, has_streamed_text
            try:
                if event.type == "thinking":
                    if not has_thinking:
                        _sys.stderr.write("\033[2;3m")
                        has_thinking = True
                    _sys.stderr.write(event.content)
                    _sys.stderr.flush()
                elif event.type == "text":
                    if has_thinking:
                        _sys.stderr.write("\033[0m\n")
                        _sys.stderr.flush()
                        has_thinking = False
                    has_streamed_text = True
                    _sys.stdout.write(event.content)
                    _sys.stdout.flush()
                elif event.type == "tool_call":
                    if has_thinking:
                        _sys.stderr.write("\033[0m\n")
                        _sys.stderr.flush()
                        has_thinking = False
                    preview = ""
                    inp = event.metadata and event.metadata.get("input")
                    if isinstance(inp, dict):
                        for v in inp.values():
                            if isinstance(v, str) and v:
                                preview = v[:60]
                                break
                    tool_name = event.content.removeprefix("mcp__syke__")
                    label = f"  ↳ {tool_name}({preview})"
                    _sys.stderr.write(f"\033[2m{label}\033[0m\n")
                    _sys.stderr.flush()
            except BrokenPipeError:
                raise

        try:
            answer, cost = run_ask(
                db=db,
                user_id=user_id,
                question=question,
                on_event=_on_event,
            )
        except BrokenPipeError:
            raise SystemExit(0)
        except Exception as e:
            if has_thinking:
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)
            _sys.stderr.write(f"\nAsk failed ({provider_label}): {e}\n")
            _sys.stderr.flush()
            raise SystemExit(1) from e
        finally:
            if has_thinking:
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)

        if not has_streamed_text and answer and answer.strip():
            _sys.stdout.write(f"\n{answer}\n")
            _sys.stdout.flush()
        elif has_streamed_text:
            _sys.stdout.write("\n")
            _sys.stdout.flush()

        if cost:
            duration_ms = cost.get("duration_ms")
            secs = float(duration_ms) / 1000 if isinstance(duration_ms, int | float) else 0.0
            usd_raw = cost.get("cost_usd")
            usd = float(usd_raw) if isinstance(usd_raw, int | float) else 0.0
            input_tokens = cost.get("input_tokens")
            output_tokens = cost.get("output_tokens")
            total_tokens = sum(
                token_count
                for token_count in (input_tokens, output_tokens)
                if isinstance(token_count, int)
            )
            tool_calls = cost.get("tool_calls")
            footer = f"\033[2m{provider_label} · {secs:.1f}s · ${usd:.4f} · {total_tokens} tokens"
            if isinstance(tool_calls, int):
                footer += f" · {tool_calls} tools"
            _sys.stderr.write(f"{footer}\033[0m\n")
    finally:
        _signal.signal(_signal.SIGTERM, prev_handler)
        db.close()


@cli.command()
@click.argument("text", required=False)
@click.option("--tag", "-t", multiple=True, help="Tag(s) for categorization")
@click.option("--source", "-s", default="manual", help="Source label (default: manual)")
@click.option(
    "--title",
    default=None,
    help="Event title (auto-generated from first line if omitted)",
)
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Parse TEXT or stdin as a single JSON event",
)
@click.option(
    "--jsonl",
    "use_jsonl",
    is_flag=True,
    help="Parse stdin as newline-delimited JSON events (batch)",
)
@click.pass_context
def record(
    ctx: click.Context,
    text: str | None,
    tag: tuple[str, ...],
    source: str,
    title: str | None,
    use_json: bool,
    use_jsonl: bool,
) -> None:
    """Record an observation, note, or research dump into Syke.

    Accepts plain text as an argument, or piped stdin for longer content.

    Examples:
      syke record "Prefers concise answers"
      echo "Long research notes..." | syke record
      syke record --json '{"text": "...", "tags": ["work"]}'
      cat events.jsonl | syke record --jsonl
    """
    from syke.observe.importers import IngestGateway

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        gw = IngestGateway(db, user_id)

        # --- JSONL batch mode: read lines from stdin ---
        if use_jsonl:
            import json as _json

            if not sys.stdin.isatty():
                lines = sys.stdin.read().strip().splitlines()
            elif text:
                lines = text.strip().splitlines()
            else:
                console.print("[red]--jsonl requires piped input or text argument[/red]")
                raise SystemExit(1)

            events = []
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(_json.loads(line))
                except _json.JSONDecodeError as e:
                    console.print(f"[red]Line {i + 1}: invalid JSON — {e}[/red]")
                    raise SystemExit(1) from None

            if not events:
                console.print("[dim]No events to record.[/dim]")
                return

            result = gw.push_batch(events)
            console.print(
                f"Recorded [green]{result['inserted']}[/green] events"
                f" ({result['duplicates']} duplicates, {result['filtered']} filtered)"
            )
            return

        # --- JSON single mode: parse one structured event ---
        if use_json:
            import json as _json

            raw = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
            if not raw:
                console.print("[red]--json requires a JSON string as argument or stdin[/red]")
                raise SystemExit(1)

            try:
                ev = _json.loads(raw)
            except _json.JSONDecodeError as e:
                console.print(f"[red]Invalid JSON: {e}[/red]")
                raise SystemExit(1) from None

            result = cast(
                dict[str, object],
                gw.push(
                    source=ev.get("source", source),
                    event_type=ev.get("event_type", "observation"),
                    title=ev.get("title", ""),
                    content=ev.get("text", ev.get("content", "")),
                    timestamp=ev.get("timestamp"),
                    metadata={"tags": ev.get("tags", list(tag))} if ev.get("tags") or tag else None,
                    external_id=ev.get("external_id"),
                ),
            )
            if result["status"] == "ok":
                event_id = cast(str, result.get("event_id", ""))
                console.print(f"Recorded. [dim]({event_id[:8]})[/dim]")
            elif result["status"] == "duplicate":
                console.print("[dim]Already recorded (duplicate).[/dim]")
            elif result["status"] == "filtered":
                console.print(
                    f"[yellow]Filtered:[/yellow] {result.get('reason', 'content filter')}"
                )
            else:
                console.print(f"[red]Error:[/red] {result.get('error', 'unknown')}")
                raise SystemExit(1)
            return

        # --- Plain text mode: argument or stdin ---
        content = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not content:
            console.print("[red]Nothing to record. Pass text as argument or pipe stdin.[/red]")
            console.print('[dim]  syke record "your observation"[/dim]')
            console.print('[dim]  echo "content" | syke record[/dim]')
            raise SystemExit(1)

        # Auto-generate title from first line if not provided
        if not title:
            first_line = content.split("\n")[0].strip()
            title = first_line[:120] if len(first_line) > 120 else first_line

        metadata = cast(dict[str, object] | None, {"tags": list(tag)} if tag else None)

        result = cast(
            dict[str, object],
            gw.push(
                source=source,
                event_type="observation",
                title=title or "",
                content=content,
                metadata=metadata,
            ),
        )

        if result["status"] == "ok":
            event_id = cast(str, result.get("event_id", ""))
            console.print(f"Recorded. [dim]({event_id[:8]})[/dim]")
        elif result["status"] == "duplicate":
            console.print("[dim]Already recorded (duplicate).[/dim]")
        elif result["status"] == "filtered":
            console.print(f"[yellow]Filtered:[/yellow] {result.get('reason', 'content filter')}")
        else:
            console.print(f"[red]Error:[/red] {result.get('error', 'unknown')}")
            raise SystemExit(1)
    finally:
        db.close()


@cli.command(hidden=True)
@click.pass_context
def detect(ctx: click.Context) -> None:
    """Detect available data sources on this machine."""
    import os as _os
    from pathlib import Path as _Path

    console.print("\n[bold]Detecting data sources...[/bold]\n")

    sources = []

    # Claude Code sessions
    claude_dir = _Path(_os.path.expanduser("~/.claude"))
    transcripts = claude_dir / "transcripts"
    projects = claude_dir / "projects"
    cc_sessions = 0
    if transcripts.exists():
        cc_sessions += len(list(transcripts.glob("*.jsonl")))
    if projects.exists():
        for d in projects.iterdir():
            if d.is_dir():
                cc_sessions += len(list(d.glob("*.jsonl")))
    if cc_sessions > 0:
        sources.append(("claude-code", f"{cc_sessions} session files", "~/.claude/"))
        console.print(
            f"  [green]FOUND[/green]  claude-code    {cc_sessions} session files in ~/.claude/"
        )

    # ChatGPT exports
    downloads = _Path(_os.path.expanduser("~/Downloads"))
    chatgpt_zips = list(downloads.glob("*chatgpt*.zip")) + list(downloads.glob("*ChatGPT*.zip"))
    # Also check for the hash-named exports from OpenAI
    for zf in downloads.glob("*.zip"):
        if zf.stat().st_size > 100_000_000 and zf not in chatgpt_zips:  # >100MB zips
            # Peek inside for conversations.json
            import zipfile

            try:
                with zipfile.ZipFile(zf) as z:
                    if "conversations.json" in z.namelist():
                        chatgpt_zips.append(zf)
            except (zipfile.BadZipFile, OSError):
                pass
    if chatgpt_zips:
        for zf in chatgpt_zips:
            size_mb = zf.stat().st_size / 1024 / 1024
            sources.append(("chatgpt", f"{size_mb:.0f} MB", str(zf)))
            console.print(f"  [green]FOUND[/green]  chatgpt        {size_mb:.0f} MB — {zf.name}")

    if not sources:
        console.print("[yellow]No data sources detected.[/yellow]")
    else:
        console.print(f"\n[bold]{len(sources)} source(s) available.[/bold]")
        console.print("[dim]Run: syke setup --user <name>[/dim]")


def _term_menu_select(entries: list[str], title: str, default_index: int = 0) -> int | None:
    """Arrow-key selection menu with non-TTY fallback.

    Returns the selected index, or None if the user cancelled / non-interactive.
    """
    import sys

    if not sys.stdin.isatty():
        # Fallback: numbered list for CI / pipes / non-TTY
        for i, entry in enumerate(entries, 1):
            click.echo(f"  [{i}] {entry}")
        try:
            pick = click.prompt(
                "  Select",
                type=click.IntRange(1, len(entries)),
                default=default_index + 1,
            )
            return pick - 1
        except (click.Abort, EOFError):
            return None

    try:
        from simple_term_menu import TerminalMenu

        menu = TerminalMenu(
            entries,
            title=title,
            menu_cursor="  ▸ ",
            menu_cursor_style=("fg_yellow", "bold"),
            menu_highlight_style=("fg_yellow", "bold"),
            cursor_index=default_index,
            cycle_cursor=True,
        )
        result = menu.show()
        if result is None:
            return None
        # show() returns int for single-select, tuple for multi-select
        return result if isinstance(result, int) else result[0]
    except Exception:
        # Terminal doesn't support menus — fall back to numbered list
        for i, entry in enumerate(entries, 1):
            click.echo(f"  [{i}] {entry}")
        try:
            pick = click.prompt(
                "  Select",
                type=click.IntRange(1, len(entries)),
                default=default_index + 1,
            )
            return pick - 1
        except (click.Abort, EOFError):
            return None


def _setup_provider_interactive() -> bool:
    """Detect all available providers and let user pick one. Always shows the picker."""
    import sys

    from syke.llm import AuthStore
    from syke.llm.codex_auth import read_codex_auth

    store = AuthStore()
    current_active = store.get_active_provider()

    # Discover all providers and their readiness
    # (id, label, ready) — ready means credentials exist and provider is usable now
    providers: list[tuple[str, str, bool]] = []

    # Codex first — recommended, uses existing ChatGPT account
    codex_creds = read_codex_auth()
    has_codex = False
    codex_label = "Codex — run 'codex login' first"
    if codex_creds is not None:
        if codex_creds.is_expired:
            from syke.llm.codex_auth import refresh_codex_token

            refreshed = refresh_codex_token(codex_creds)
            if refreshed:
                has_codex = True
                codex_label = "Codex — ChatGPT account (recommended)"
            else:
                codex_label = "Codex — token expired, run 'codex login' to refresh"
        else:
            has_codex = True
            codex_label = "Codex — ChatGPT account (recommended)"
    providers.append(("codex", codex_label, has_codex))

    # API key providers — explicit, safe
    for pid, name in [("openrouter", "OpenRouter"), ("zai", "z.ai"), ("kimi", "Kimi")]:
        has_key = store.get_token(pid) is not None
        providers.append(
            (
                pid,
                name if has_key else f"{name} — enter API key",
                has_key,
            )
        )

    # Pi-native providers
    from syke.config import CFG

    for pid, name in [
        ("azure", "Azure OpenAI"),
        ("openai", "OpenAI API"),
        ("ollama", "Ollama (local)"),
        ("vllm", "vLLM (local)"),
        ("llama-cpp", "llama.cpp (local)"),
    ]:
        pcfg = CFG.providers.get(pid, {})
        has_config = bool(pcfg.get("model") or store.get_token(pid))
        providers.append(
            (
                pid,
                f"{name} (Pi runtime)"
                if has_config
                else f"{name} (Pi runtime) — run syke auth set {pid}",
                has_config,
            )
        )

    # Non-TTY (agent/pipe/CI): print inventory, don't auto-select
    if not sys.stdin.isatty():
        console.print("\n  Detected providers:")
        for pid, label, ready in providers:
            if ready:
                tag = "[green]ready[/green]"
            else:
                tag = "[yellow]no key[/yellow]"
            active = " (active)" if pid == current_active and ready else ""
            console.print(f"    [{tag}]  {pid}  — {label}{active}")
        console.print(
            "\n  [dim]No provider selected."
            " Use --provider <id> to choose, or run interactively.[/dim]"
        )
        return False

    # Build menu entries with status tags
    entries: list[str] = []
    for pid, label, ready in providers:
        tag = ""
        if pid == current_active and ready:
            tag = "  (active)"
        elif ready:
            tag = "  ✓"
        entries.append(f"{pid}  —  {label}{tag}")
    entries.append("Skip for now")

    # Pre-select: current active if ready > first ready > codex
    default_idx = 0
    active_found = False
    if current_active:
        for i, (pid, _, ready) in enumerate(providers):
            if pid == current_active and ready:
                default_idx = i
                active_found = True
                break
    if not active_found:
        for i, (pid, _, ready) in enumerate(providers):
            if ready:
                default_idx = i
                break

    idx = _term_menu_select(entries, title="\n  Select a provider:\n", default_index=default_idx)

    if idx is None or idx == len(entries) - 1:
        return False

    selected_pid, _, is_ready = providers[idx]

    if not is_ready:
        if selected_pid == "codex":
            cmd = "codex login"
            console.print(f"\n  Run [bold]{cmd}[/bold] and then re-run [bold]syke setup[/bold].")
            return False
        elif selected_pid in ("azure", "openai", "ollama", "vllm", "llama-cpp"):
            return _setup_pi_provider_flow(selected_pid)
        else:
            return _setup_api_key_flow(selected_pid)

    store.set_active_provider(selected_pid)
    console.print(f"\n  [green]✓[/green]  Provider: [bold]{selected_pid}[/bold]")
    return True


def _setup_pi_provider_flow(provider_id: str) -> bool:
    """Prompt for Pi runtime provider fields inline and store config."""
    from syke.config_file import write_provider_config
    from syke.llm import AuthStore

    store = AuthStore()
    provider_config: dict[str, str] = {}

    # Prompt for fields based on provider type
    if provider_id == "azure":
        endpoint = click.prompt("\n  Azure endpoint URL", type=str)
        if not endpoint.strip():
            return False
        provider_config["endpoint"] = endpoint.strip()

        model = click.prompt("  Model name (e.g. gpt-5, gpt-5-mini)", type=str)
        if not model.strip():
            return False
        provider_config["model"] = model.strip()

        api_key = click.prompt("  API key", hide_input=True)
        if not api_key.strip():
            return False

        api_version = click.prompt(
            "  API version (optional)",
            type=str,
            default="",
        )
        if api_version.strip():
            provider_config["api_version"] = api_version.strip()

    elif provider_id == "openai":
        api_key = click.prompt("\n  API key", hide_input=True)
        if not api_key.strip():
            return False

        model = click.prompt("  Model name (e.g. gpt-5.4, gpt-5-mini)", type=str)
        if not model.strip():
            return False
        provider_config["model"] = model.strip()

    elif provider_id == "ollama":
        model = click.prompt("\n  Model name (e.g. deepseek-r1, qwen3)", type=str)
        if not model.strip():
            return False
        provider_config["model"] = model.strip()

        base_url = click.prompt(
            "  Base URL (optional, default: http://localhost:11434)",
            type=str,
            default="",
        )
        if base_url.strip():
            provider_config["base_url"] = base_url.strip()

        api_key = None  # ollama doesn't require API key

    elif provider_id in ("vllm", "llama-cpp"):
        base_url = click.prompt("\n  Base URL (e.g. http://localhost:8000)", type=str)
        if not base_url.strip():
            return False
        provider_config["base_url"] = base_url.strip()

        model = click.prompt("  Model name", type=str)
        if not model.strip():
            return False
        provider_config["model"] = model.strip()

        api_key = None  # vllm and llama-cpp don't require API key

    else:
        return False

    # Write non-secret config to config.toml
    if provider_config:
        write_provider_config(provider_id, provider_config)

    # Store API key if provided
    if api_key:
        store.set_token(provider_id, api_key.strip())

    # Set as active provider
    store.set_active_provider(provider_id)
    console.print(f"\n  [green]✓[/green]  Provider: [bold]{provider_id}[/bold]")
    return True


def _setup_api_key_flow(provider_id: str | None = None) -> bool:
    """Prompt for API key and store it. Returns True if configured."""
    from syke.llm import AuthStore

    if provider_id is None:
        api_providers = ["openrouter", "zai"]
        entries = [f"{pid}" for pid in api_providers]
        idx = _term_menu_select(entries, title="\n  Which provider?\n")
        if idx is None:
            return False
        provider_id = api_providers[idx]

    api_key = click.prompt(
        f"\n  Enter your {provider_id} API key",
        hide_input=True,
    )
    if not api_key.strip():
        return False

    store = AuthStore()
    store.set_token(provider_id, api_key.strip())
    store.set_active_provider(provider_id)
    console.print(f"\n  [green]✓[/green]  Provider: [bold]{provider_id}[/bold]")
    return True


@cli.command()
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Auto-consent confirmations (daemon install), never auto-selects provider",
)
@click.option("--skip-daemon", is_flag=True, help="Skip daemon install (testing only)")
@click.pass_context
def setup(ctx: click.Context, yes: bool, skip_daemon: bool) -> None:
    """Detect sources, ingest data, start daemon. Synthesis runs on first daemon tick.

    Human: syke setup          (interactive provider picker)
    Agent: syke setup --provider codex --yes  (explicit provider, no prompts)
    """
    import os as _os
    import subprocess
    from pathlib import Path as _Path

    user_id = ctx.obj["user"]
    console.print(f"\n[bold]Syke Setup[/bold] — user: [cyan]{user_id}[/cyan]\n")

    # Step 1: Choose LLM provider
    console.print("[bold]Step 1:[/bold] LLM provider")
    from syke.llm.env import resolve_provider

    cli_provider = ctx.obj.get("provider")
    has_provider = False

    if cli_provider:
        # Explicit --provider flag — use it directly
        try:
            provider = resolve_provider(cli_provider=cli_provider)
            has_provider = True
            console.print(f"  [green]✓[/green]  Provider: [bold]{provider.id}[/bold]")
        except (ValueError, RuntimeError) as e:
            console.print(f"  [red]✗[/red]  {e}")
    else:
        # Always show the picker — detect, present, let user choose
        has_provider = _setup_provider_interactive()

    if not has_provider:
        console.print(
            "\n  [yellow]Skipping provider setup.[/yellow]"
            " Ingestion will run, but synthesis requires an LLM provider."
        )
        console.print("  [dim]Configure later: syke auth set <provider> --api-key <key>[/dim]")

    # Step 2: Detect and ingest sources
    console.print("\n[bold]Step 2:[/bold] Detecting and ingesting data sources...\n")
    db = get_db(user_id)

    try:
        ingested_count = 0

        def _source_msg(name: str, source_key: str, new_count: int, unit: str = "events") -> None:
            """Print per-source result: new count + existing total."""
            existing = db.count_events(user_id, source=source_key)
            if new_count > 0:
                console.print(
                    f"  [green]OK[/green]  {name}: +{new_count} new {unit} ({existing} total)"
                )
            elif existing > 0:
                console.print(f"  [green]OK[/green]  {name}: up to date ({existing} {unit})")
            else:
                console.print(f"  [green]OK[/green]  {name}: {new_count} {unit}")

        from syke.observe.bootstrap import ensure_adapters
        from syke.observe.registry import HarnessRegistry
        from syke.metrics import MetricsTracker

        _bootstrap_results = ensure_adapters(user_id)
        _ingestible_sources = {
            _result.source
            for _result in _bootstrap_results
            if _result.status in {"existing", "generated"}
        }
        for _bootstrap in _bootstrap_results:
            if _bootstrap.status == "generated":
                console.print(f"  [dim]Bootstrapped adapter: {_bootstrap.source}[/dim]")
            elif _bootstrap.status == "failed":
                console.print(
                    f"  [yellow]WARN[/yellow]  {_bootstrap.source} adapter bootstrap: {_bootstrap.detail}"
                )

        setup_registry = HarnessRegistry()
        for _desc in setup_registry.active_harnesses():
            _src = _desc.source
            if _src not in _ingestible_sources:
                continue
            _adapter = setup_registry.get_adapter(_src, db, user_id)
            if _adapter is None:
                continue
            try:
                console.print(f"  [cyan]Ingesting {_src}...[/cyan]")
                tracker = MetricsTracker(user_id)
                with tracker.track(f"ingest_{_src}") as metrics:
                    _result = _adapter.ingest()
                    metrics.events_processed = _result.events_count
                _source_msg(_src, _src, _result.events_count, "events")
                ingested_count += _result.events_count
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  {_src}: {e}")

        # ChatGPT export
        downloads = _Path(_os.path.expanduser("~/Downloads"))
        chatgpt_zip = None
        for zf in sorted(downloads.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            if zf.stat().st_size > 100_000_000:
                import zipfile

                try:
                    with zipfile.ZipFile(zf) as z:
                        if "conversations.json" in z.namelist():
                            chatgpt_zip = zf
                            break
                except (zipfile.BadZipFile, OSError):
                    pass
        if chatgpt_zip:
            console.print(f"  [cyan]Ingesting ChatGPT export...[/cyan] ({chatgpt_zip.name})")
            from syke.observe.importers import ChatGPTAdapter
            from syke.metrics import MetricsTracker

            tracker = MetricsTracker(user_id)
            with tracker.track("ingest_chatgpt") as metrics:
                adapter = ChatGPTAdapter(db, user_id)
                result = adapter.ingest(file_path=str(chatgpt_zip))
                metrics.events_processed = result.events_count
            _source_msg("ChatGPT", "chatgpt", result.events_count, "conversations")
            ingested_count += result.events_count

        # GitHub (public — no consent needed)
        # Try to detect username from git config or gh CLI
        gh_username = None
        try:
            r = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                # Try GitHub username from gh CLI
                r2 = subprocess.run(
                    ["gh", "api", "user", "--jq", ".login"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r2.returncode == 0 and r2.stdout.strip():
                    gh_username = r2.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        if gh_username:
            console.print(f"  [dim]GitHub username detected: @{gh_username}[/dim]")

        # Check total events in DB (including previously ingested)
        total_in_db = db.count_events(user_id)
        if total_in_db == 0 and ingested_count == 0:
            console.print("[yellow]No data sources found to ingest.[/yellow]")
            return

        # Step 2b: Pi runtime
        console.print("\n[bold]Step 2b:[/bold] Pi agent runtime\n")
        try:
            from syke.llm.pi_client import ensure_pi_binary, get_pi_version

            pi_path = ensure_pi_binary()
            ver = get_pi_version(install=False)
            console.print(f"  [green]OK[/green]  Pi runtime v{ver}")
            console.print(f"  [dim]Launcher:[/dim] {pi_path}")
        except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            console.print(f"  [yellow]WARN[/yellow]  Pi runtime: {e}")
            console.print("  [dim]Syke runtime will not work until Node.js is available.[/dim]")

        # Step 3: Background daemon (synthesis runs on first tick)
        daemon_started = False
        if not skip_daemon:
            console.print("\n[bold]Step 3:[/bold] Background sync daemon\n")
            try:
                from syke.daemon.daemon import install_and_start, is_running

                running, pid = is_running()
                if running:
                    console.print(f"  [green]OK[/green]  Daemon already running (PID {pid})")
                    daemon_started = True
                else:
                    install_and_start(user_id, interval=900)
                    daemon_started = True
                    console.print("  [green]OK[/green]  Daemon installed — syncs every 15 minutes.")
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  Daemon install failed: {e}")
                console.print("  [dim]You can install manually with: syke daemon start[/dim]")

        # Final summary
        console.print("\n[bold green]Setup complete.[/bold green]")
        if ingested_count > 0:
            console.print(f"  +{ingested_count} new events ({total_in_db} total)")
        else:
            console.print(f"  {total_in_db} events collected")

        if daemon_started and has_provider:
            console.print(
                "  Daemon installed — syncs every 15 minutes, synthesis runs automatically."
            )
            console.print("  Run [bold]syke context[/bold] in a few minutes to see your memex.")
        elif daemon_started:
            console.print("  Daemon installed — syncs every 15 minutes.")
            console.print("  Configure a provider to enable synthesis:")
            console.print("  [dim]syke auth set <provider> --api-key <key>[/dim]")
        elif has_provider:
            console.print("  Run [bold]syke sync[/bold] to synthesize your memex.")
        else:
            console.print("  Configure a provider, then run [bold]syke sync[/bold].")
            console.print("  [dim]syke auth set <provider> --api-key <key>[/dim]")

        console.print()
        console.print('[dim]Useful commands: syke ask "...", syke context, syke doctor[/dim]')

    finally:
        db.close()


@cli.command()
@click.pass_context
def sync(ctx: click.Context) -> None:
    """Sync new data and run synthesis.

    Pulls new events from all connected sources, then runs an incremental
    synthesis if enough new data is found (minimum 5 events).
    """
    from syke.sync import run_sync

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        sources = db.get_sources(user_id)
        if not sources:
            console.print("[yellow]No data yet. Run: syke setup --user <name>[/yellow]")
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


# ---------------------------------------------------------------------------
# syke auth — provider credential management
# ---------------------------------------------------------------------------


@cli.group(invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage LLM provider credentials."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(auth_status)


@auth.command("status")
@click.pass_context
def auth_status(ctx: click.Context) -> None:
    """Show active provider and configured credentials."""
    from syke.llm import PROVIDERS, AuthStore
    from syke.llm.env import _resolve_token

    store = AuthStore()
    active = store.get_active_provider()
    stored = store.list_providers()

    if active:
        source = "auth.json"
    else:
        source = None

    if active:
        console.print(f"[bold]Active provider:[/bold] {active} [dim]({source})[/dim]")
    else:
        console.print(
            "[yellow]No provider configured.[/yellow] Run [bold]syke auth set <provider> --api-key <key>[/bold]"
            " or [bold]syke auth use codex[/bold]."
        )

    # Detect externally-credentialed providers (codex)
    from syke.llm.codex_auth import read_codex_auth

    codex_creds = read_codex_auth()
    has_codex = codex_creds is not None and not codex_creds.is_expired

    configured_pids: set[str] = set(stored.keys())
    if has_codex:
        configured_pids.add("codex")

    if configured_pids:
        from syke.config import CFG

        console.print("\n[bold]Configured:[/bold]")

        if has_codex and "codex" not in stored:
            marker = " [green]← active[/green]" if active == "codex" else ""
            console.print(f"  codex: [dim](~/.codex/auth.json)[/dim]{marker}")

        for pid, info in stored.items():
            marker = " [green]← active[/green]" if info["active"] else ""
            spec = PROVIDERS.get(pid)
            mode_tag = ""
            config_detail = ""
            if spec and spec.pi_provider:
                mode_tag = " [dim](Pi runtime)[/dim]"
                pcfg = CFG.providers.get(pid, {})
                parts = []
                if pcfg.get("endpoint"):
                    parts.append(f"endpoint: {pcfg['endpoint']}")
                if pcfg.get("base_url"):
                    parts.append(f"base_url: {pcfg['base_url']}")
                if pcfg.get("model"):
                    parts.append(f"model: {pcfg['model']}")
                if parts:
                    config_detail = f" | {', '.join(parts)}"
            console.print(f"  {pid}: {info['credential']}{mode_tag}{config_detail}{marker}")

    unconfigured = [pid for pid in sorted(PROVIDERS) if pid not in configured_pids]
    if unconfigured:
        console.print(f"\n[dim]Available: {', '.join(unconfigured)}[/dim]")


@auth.command("set")
@click.argument("provider")
@click.option("--api-key", default=None, help="API key / auth token (required for cloud providers)")
@click.option("--endpoint", default=None, help="API endpoint URL (azure)")
@click.option("--base-url", default=None, help="Base URL (ollama, vllm, llama-cpp)")
@click.option("--model", default=None, help="Model name (e.g. gpt-5, deepseek-r1)")
@click.option("--api-version", default=None, help="API version (azure, e.g. 2024-02-01)")
@click.option(
    "--use", "set_active", is_flag=True, default=False, help="Set as active provider after storing"
)
@click.pass_context
def auth_set(
    ctx: click.Context,
    provider: str,
    api_key: str | None,
    endpoint: str | None,
    base_url: str | None,
    model: str | None,
    api_version: str | None,
    set_active: bool,
) -> None:
    """Store credentials and config for a provider."""
    from syke.config_file import write_provider_config
    from syke.llm import PROVIDERS, AuthStore

    if provider not in PROVIDERS:
        valid = ", ".join(sorted(PROVIDERS))
        console.print(f"[red]Unknown provider '{provider}'. Valid: {valid}[/red]")
        raise SystemExit(1)

    spec = PROVIDERS[provider]
    store = AuthStore()

    # Store API key in auth.json (secrets only)
    if api_key:
        store.set_token(provider, api_key)
    elif spec.token_env_var:
        # Cloud providers may also source auth from env vars.
        console.print(
            f"[yellow]No --api-key provided. Set {spec.token_env_var} env var or re-run with --api-key.[/yellow]"
        )

    # Build non-secret config for config.toml
    provider_config: dict[str, str] = {}
    if endpoint:
        provider_config["endpoint"] = endpoint
    if base_url:
        provider_config["base_url"] = base_url
    if model:
        provider_config["model"] = model
    if api_version:
        provider_config["api_version"] = api_version

    # Write non-secret config to config.toml
    if provider_config:
        write_provider_config(provider, provider_config)

    # Set as active if --use flag
    if set_active:
        store.set_active_provider(provider)
        console.print(
            f"[green]✓[/green] Config stored and [bold]{provider}[/bold] set as active provider."
        )
    else:
        console.print(f"[green]✓[/green] Config stored for [bold]{provider}[/bold].")


@auth.command("use")
@click.argument("provider")
@click.pass_context
def auth_use(ctx: click.Context, provider: str) -> None:
    """Set the active LLM provider."""
    from syke.llm import PROVIDERS, AuthStore

    if provider not in PROVIDERS:
        valid = ", ".join(sorted(PROVIDERS))
        console.print(f"[red]Unknown provider '{provider}'. Valid: {valid}[/red]")
        raise SystemExit(1)

    spec = PROVIDERS[provider]
    store = AuthStore()

    if provider == "codex":
        from syke.llm.codex_auth import read_codex_auth

        creds = read_codex_auth()
        if creds is None:
            console.print(
                "[red]No Codex credentials found.[/red] Run [bold]codex login[/bold] first."
            )
            raise SystemExit(1)
        store.set_active_provider(provider)
        console.print(
            f"[green]\u2713[/green] Active provider set to [bold]{provider}[/bold]."
            f" Using ~/.codex/auth.json credentials."
        )
    else:
        token = _resolve_token(spec)
        if token is None:
            console.print(
                f"[yellow]No credentials for {provider}.[/yellow]"
                f" Run [bold]syke auth set {provider} --api-key <key>[/bold] first."
            )
            raise SystemExit(1)
        if spec.token_env_var and os.getenv(spec.token_env_var):
            console.print(
                f"[dim]Using {spec.token_env_var} environment variable for {provider}.[/dim]"
            )
        if not spec.token_env_var:
            from syke.config import CFG

            provider_cfg = CFG.providers.get(provider, {})
            if not provider_cfg.get("model"):
                console.print(
                    f"[yellow]No config for {provider}.[/yellow]"
                    f" Run [bold]syke auth set {provider}[/bold] first."
                )
                raise SystemExit(1)
        store.set_active_provider(provider)
        console.print(f"[green]\u2713[/green] Active provider set to [bold]{provider}[/bold].")


@auth.command("unset")
@click.argument("provider")
@click.pass_context
def auth_unset(ctx: click.Context, provider: str) -> None:
    """Remove stored credentials for a provider."""
    from syke.llm import AuthStore

    store = AuthStore()
    removed = store.remove_token(provider)
    if removed:
        console.print(f"[green]✓[/green] Credentials removed for [bold]{provider}[/bold].")
    else:
        console.print(f"[dim]No credentials stored for {provider}.[/dim]")


# ---------------------------------------------------------------------------
# syke config — configuration file management
# ---------------------------------------------------------------------------


@cli.group(invoke_without_command=True)
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
    from syke.config_file import CONFIG_PATH

    if raw:
        if CONFIG_PATH.exists():
            console.print(CONFIG_PATH.read_text())
        else:
            console.print(f"[dim]No config file at {CONFIG_PATH}[/dim]")
        return

    from syke import config as c

    console.print("[bold]Syke Configuration[/bold]")
    console.print(
        f"  [dim]File:[/dim] {CONFIG_PATH}"
        + (" [green](loaded)[/green]" if CONFIG_PATH.exists() else " [dim](defaults)[/dim]")
    )
    console.print()

    # ── Resolve active provider ────────────────────────────────────
    provider_id, provider_source, provider_details = _resolve_provider_display()
    console.print("  [bold]Provider[/bold]")
    if provider_id:
        console.print(f"    active: [cyan]{provider_id}[/cyan] [dim]({provider_source})[/dim]")
        for key, val in provider_details.items():
            console.print(f"    {key}: [cyan]{val}[/cyan]")
    else:
        console.print(
            "    active: [yellow](none)[/yellow] — run syke setup or syke auth set <provider>"
        )
    console.print()

    # ── Effective model per task ────────────────────────────────────
    eff_sync = _effective_model(c.SYNC_MODEL, provider_id)
    eff_ask = _effective_model(c.ASK_MODEL, provider_id)
    eff_rebuild = _effective_model(c.REBUILD_MODEL, provider_id)

    _section(
        "Synthesis",
        {
            "model": eff_sync,
            "budget": f"${c.SYNC_BUDGET:.2f} / run",
            "max_turns": c.SYNC_MAX_TURNS,
            "thinking": f"{c.SYNC_THINKING} tokens",
            "timeout": f"{c.SYNC_TIMEOUT}s",
            "threshold": f"{c.SYNC_EVENT_THRESHOLD} new events",
            "first run": f"${c.SETUP_SYNC_BUDGET:.2f} / {c.SETUP_SYNC_MAX_TURNS} turns",
        },
    )
    _section(
        "Ask",
        {
            "model": eff_ask,
            "budget": f"${c.ASK_BUDGET:.2f} / run",
            "max_turns": c.ASK_MAX_TURNS,
            "timeout": f"{c.ASK_TIMEOUT}s",
        },
    )
    _section(
        "Rebuild",
        {
            "model": eff_rebuild,
            "budget": f"${c.REBUILD_BUDGET:.2f} / run",
            "max_turns": c.REBUILD_MAX_TURNS,
            "thinking": f"{c.REBUILD_THINKING} tokens",
        },
    )
    _section(
        "Daemon",
        {
            "interval": f"{c.DAEMON_INTERVAL}s ({c.DAEMON_INTERVAL // 60} min)",
        },
    )

    # ── Identity (compact) ─────────────────────────────────────────
    from syke.time import resolve_user_tz

    tz = resolve_user_tz()
    tz_display = str(tz) if str(tz) != c.SYKE_TIMEZONE else c.SYKE_TIMEZONE
    if c.SYKE_TIMEZONE == "auto":
        tz_display = f"{tz} (auto)"

    _section(
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


def _section(title: str, items: dict[str, object]) -> None:
    console.print(f"  [bold]{title}[/bold]")
    for key, val in items.items():
        console.print(f"    {key}: [cyan]{val}[/cyan]")
    console.print()


def _resolve_provider_display() -> tuple[str | None, str, dict[str, str]]:
    """Resolve active provider for display: (id, source, {detail_key: value})."""
    from syke.config import CFG
    from syke.llm import PROVIDERS, AuthStore

    store = AuthStore()
    active = store.get_active_provider()
    details: dict[str, str] = {}

    if not active:
        return None, "", {}

    source = "auth.json"
    spec = PROVIDERS.get(active)

    if spec and spec.pi_provider:
        pcfg = CFG.providers.get(active, {})
        if pcfg.get("endpoint"):
            details["endpoint"] = pcfg["endpoint"]
        if pcfg.get("base_url"):
            details["base_url"] = pcfg["base_url"]
        if pcfg.get("model"):
            details["runtime model"] = pcfg["model"]
        details["routing"] = "Pi runtime"
    elif spec and spec.base_url:
        details["base_url"] = spec.base_url

    return active, source, details


def _effective_model(config_model: str | None, provider_id: str | None) -> str:
    """What model actually runs under the active Pi provider."""
    from syke.config import CFG
    from syke.llm import PROVIDERS

    if not provider_id:
        return config_model or "(none)"

    if provider_id == "codex":
        from syke.llm.codex_auth import get_codex_model

        return get_codex_model()

    spec = PROVIDERS.get(provider_id)
    if spec and spec.pi_provider:
        pcfg = CFG.providers.get(provider_id, {})
        upstream = pcfg.get("model")
        if upstream:
            return upstream

    return config_model or "(sdk default)"


# ---------------------------------------------------------------------------
# syke daemon — background sync
# ---------------------------------------------------------------------------


@cli.group()
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
    """Start background sync daemon (macOS LaunchAgent)."""
    from syke.daemon.daemon import install_and_start, is_running

    user_id = ctx.obj["user"]
    # Check if already running
    running, pid = is_running()
    if running:
        console.print(f"[yellow]Daemon already running (PID {pid})[/yellow]")
        return
    console.print(f"[bold]Starting daemon[/bold] — user: [cyan]{user_id}[/cyan]")
    console.print(f"  Sync interval: {interval}s ({interval // 60} minutes)")
    install_and_start(user_id, interval)

    console.print(f"[green]✓[/green] Daemon started. Sync runs every {interval // 60} minutes.")
    console.print("  Check status: syke daemon status")
    console.print("  View logs:    syke daemon logs")


@daemon.command("stop")
@click.pass_context
def daemon_stop(ctx: click.Context) -> None:
    """Stop background sync daemon."""
    from syke.daemon.daemon import is_running, stop_and_unload

    running, pid = is_running()
    if not running:
        console.print("[dim]Daemon not running[/dim]")
        return
    console.print(f"[bold]Stopping daemon[/bold] (PID {pid})")
    stop_and_unload()
    console.print("[green]✓[/green] Daemon stopped.")


@daemon.command("status")
@click.pass_context
def daemon_status_cmd(ctx: click.Context) -> None:
    """Check daemon status."""
    from syke.daemon.daemon import LOG_PATH, is_running
    from syke.daemon.metrics import MetricsTracker
    from syke.runtime.locator import (
        SYKE_BIN,
        describe_runtime_target,
        resolve_background_syke_runtime,
        resolve_syke_runtime,
    )

    running, pid = is_running()
    user_id = ctx.obj["user"]
    console.print("[bold]Daemon status[/bold]")
    console.print(
        f"  Running:  {'[green]yes[/green] (PID ' + str(pid) + ')' if running else '[red]no[/red]'}"
    )
    # Last sync from metrics.jsonl
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
    try:
        current_runtime = resolve_syke_runtime()
        console.print(f"  CLI:      {describe_runtime_target(current_runtime)}")
    except Exception as exc:
        console.print(f"  CLI:      [yellow]unavailable: {exc}[/yellow]")
    try:
        runtime = resolve_background_syke_runtime()
        console.print(f"  Launcher: {SYKE_BIN}")
        console.print(f"  Target:   {describe_runtime_target(runtime)}")
    except Exception as exc:
        console.print(f"  Launcher: {SYKE_BIN}  [yellow]unavailable: {exc}[/yellow]")
    # Version info (cache-only, never hits network)
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
@click.pass_context
def logs(ctx: click.Context, lines: int, follow: bool, errors: bool) -> None:
    """View daemon log output."""
    import time
    from collections import deque

    from syke.daemon.daemon import LOG_PATH

    if not LOG_PATH.exists():
        console.print(f"[yellow]No daemon log found at {LOG_PATH}[/yellow]")
        console.print("[dim]Is the daemon installed? Run: syke daemon start[/dim]")
        return

    if follow:
        with open(LOG_PATH) as f:
            f.seek(0, 2)  # seek to end
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
        for line in tail:
            console.print(line)


@cli.group(invoke_without_command=True)
@click.pass_context
def sense(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        ctx.invoke(sense_status)


@sense.command("start")
@click.option(
    "--interval",
    type=int,
    default=900,
    help="Cycle interval in seconds (default: 900 = 15 min)",
)
@click.pass_context
def sense_start(ctx: click.Context, interval: int) -> None:
    from syke.daemon.daemon import install_and_start, is_running

    user_id = ctx.obj["user"]
    running, pid = is_running()
    if running:
        console.print(f"[yellow]Sense daemon already running (PID {pid})[/yellow]")
        return

    install_and_start(user_id, interval)
    console.print(f"[green]✓[/green] Sense daemon started (cycle {interval // 60} min).")


@sense.command("stop")
@click.pass_context
def sense_stop(ctx: click.Context) -> None:
    from syke.daemon.daemon import is_running, stop_and_unload

    running, pid = is_running()
    if not running:
        console.print("[dim]Sense daemon not running[/dim]")
        return

    console.print(f"[bold]Stopping Sense daemon[/bold] (PID {pid})")
    stop_and_unload()
    console.print("[green]✓[/green] Sense daemon stopped.")


@sense.command("status")
@click.pass_context
def sense_status(ctx: click.Context) -> None:
    from syke.daemon.daemon import get_status

    _ = ctx
    console.print(get_status())


@cli.command("self-update")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def self_update(ctx: click.Context, yes: bool) -> None:
    """Upgrade syke to the latest version from PyPI."""
    import subprocess

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

    method = _detect_install_method()

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

    # Stop daemon if running so the new binary is picked up cleanly
    was_running, _ = is_running()
    if was_running:
        console.print("  Stopping daemon...")
        stop_and_unload()

    if method == "pipx":
        cmd = ["pipx", "upgrade", "syke"]
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

    console.print(f"[green]✓[/green] syke upgraded to {latest}.")


# ---------------------------------------------------------------------------
# Dashboard (bare `syke` with no subcommand)
# ---------------------------------------------------------------------------


def _show_dashboard(user_id: str) -> None:
    """Show a quick status dashboard when `syke` is invoked without a subcommand."""
    import platform

    console.print(f"[bold]Syke[/bold] v{__version__}  ·  user: {user_id}\n")

    from syke.llm.env import resolve_provider

    try:
        provider = resolve_provider()
        auth_label = f"[green]{provider.id}[/green]"
    except (ValueError, RuntimeError):
        auth_label = "[yellow]not configured[/yellow]"
    console.print(f"  Provider: {auth_label}")

    # Daemon — prefer launchd (macOS one-shot), fall back to PID
    if platform.system() == "Darwin":
        import re

        from syke.daemon.daemon import launchd_status

        launchd_out = launchd_status()
        if launchd_out is not None:
            m = re.search(r'"LastExitStatus"\s*=\s*(\d+)', launchd_out)
            exit_status = int(m.group(1)) if m else -1
            if exit_status == 0:
                daemon_label = "[green]running[/green] (launchd)"
            else:
                daemon_label = f"[yellow]registered[/yellow] (last exit: {exit_status})"
        else:
            daemon_label = "[dim]stopped[/dim]"
    else:
        from syke.daemon.daemon import is_running

        running, pid = is_running()
        if running:
            daemon_label = f"[green]running[/green] (PID {pid})"
        else:
            daemon_label = "[dim]stopped[/dim]"
    console.print(f"  Daemon:  {daemon_label}")

    # DB stats + Memex (both from DB)
    syke_db_path = user_syke_db_path(user_id)
    if syke_db_path.exists():
        db = get_db(user_id)
        try:
            count = db.count_events(user_id)
            status = db.get_status(user_id)
            last_event = status.get("latest_event_at", "never")
            console.print(f"  Events:  {count}")
            console.print(f"  Last:    {last_event or 'never'}")

            # Memex lives in the DB, not a file
            memex = db.get_memex(user_id)
            if memex:
                mem_count = db.count_memories(user_id)
                console.print(f"  Memex:   [green]synthesized[/green] ({mem_count} memories)")
            else:
                console.print("  Memex:   [yellow]not yet synthesized[/yellow] — run: syke sync")
        finally:
            db.close()
    else:
        console.print("  DB:      [dim]not initialized[/dim]")

    # Harness adapters (compact: only show detected ones)
    from syke.distribution.harness import status_all

    statuses = status_all()
    detected = [s for s in statuses if s.detected]
    if detected:
        parts = []
        for s in detected:
            if s.connected:
                parts.append(f"[green]{s.name}[/green]")
            else:
                parts.append(f"[yellow]{s.name}[/yellow]")
        console.print(f"  Agents:  {', '.join(parts)}")

    console.print("\n  Run [bold]syke --help[/bold] for commands.")


# ---------------------------------------------------------------------------
# Helper for doctor checks
# ---------------------------------------------------------------------------


def _print_check(name: str, ok: bool, detail: str) -> None:
    tag = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    console.print(f"  {tag}  {name}: {detail}")


# ---------------------------------------------------------------------------
# syke context
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown"]),
    default="markdown",
    help="Output format",
)
@click.pass_context
def context(ctx: click.Context, fmt: str) -> None:
    """Dump the current memex (synthesized identity) to stdout."""
    from syke.memory.memex import get_memex_for_injection

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        content = get_memex_for_injection(db, user_id)
        if not content:
            console.print("[dim]No memex yet. Run: syke setup[/dim]")
            return
        if fmt == "json":
            click.echo(json.dumps({"memex": content, "user": user_id}))
        else:
            click.echo(content)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# syke observe
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--watch", is_flag=True, help="Live refresh every 30 seconds")
@click.option("--days", "-d", default=7, help="Trend window in days (default: 7)")
@click.pass_context
def observe(ctx: click.Context, watch: bool, days: int) -> None:
    """The system observing itself — memory, synthesis, ingestion, evolution."""
    from syke.health import format_observe, full_observe

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
            output = format_observe(data)
            console.print(output)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# syke doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--network", is_flag=True, help="Test real API connectivity")
@click.pass_context
def doctor(ctx: click.Context, network: bool) -> None:
    """Verify Syke installation health."""
    import subprocess

    from syke.daemon.daemon import is_running, launchd_status
    from syke.runtime.locator import (
        SYKE_BIN,
        describe_runtime_target,
        resolve_background_syke_runtime,
        resolve_syke_runtime,
    )

    user_id = ctx.obj["user"]
    console.print(f"[bold]Syke Doctor[/bold]  ·  user: {user_id}\n")

    # Provider resolution
    from syke.llm.auth_store import _redact
    from syke.llm.env import build_pi_runtime_env, resolve_provider

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
        source = _resolve_source(ctx.obj.get("provider"))
        _print_check("Provider", True, f"{provider.id} (source: {source})")
        if provider.base_url:
            console.print(f"         Base URL: {provider.base_url}")
        env = build_pi_runtime_env(provider)
        visible_tokens = {
            key: value
            for key, value in env.items()
            if key.endswith("_API_KEY") and value
        }
        for env_name, token in sorted(visible_tokens.items()):
            console.print(f"         {env_name}: {_redact(token)}")
        visible_urls = {
            key: value
            for key, value in env.items()
            if key.endswith("_BASE_URL") and value
        }
        for env_name, value in sorted(visible_urls.items()):
            console.print(f"         {env_name}: {value}")
    except (ValueError, RuntimeError) as e:
        _print_check("Provider", False, str(e))

    from syke.llm.pi_client import PI_BIN, get_pi_version

    if PI_BIN.exists():
        try:
            ver = get_pi_version(install=False)
            _print_check("Pi runtime", True, f"v{ver} ({PI_BIN})")
        except Exception as e:
            _print_check("Pi runtime", False, f"binary exists but failed: {e}")
    else:
        _print_check(
            "Pi runtime",
            False,
            "not installed — run 'syke setup' (requires Node.js)",
        )

    if PI_BIN.exists():
        try:
            get_pi_version(install=False, minimal_env=True)
            _print_check("Pi cold-start", True, "minimal environment OK")
        except Exception as e:
            _print_check("Pi cold-start", False, f"minimal environment failed: {e}")

    try:
        current_runtime = resolve_syke_runtime()
        _print_check("CLI runtime", True, describe_runtime_target(current_runtime))
    except Exception as e:
        _print_check("CLI runtime", False, str(e))

    try:
        background_runtime = resolve_background_syke_runtime()
        _print_check(
            "Launcher",
            True,
            f"{SYKE_BIN} -> {describe_runtime_target(background_runtime)}",
        )
    except Exception as e:
        _print_check("Launcher", False, f"{SYKE_BIN}: {e}")

    # Database
    syke_db_path = user_syke_db_path(user_id)
    events_db_path = user_events_db_path(user_id)
    has_syke_db = syke_db_path.exists()
    has_events_db = events_db_path.exists()
    has_db = has_syke_db
    _print_check(
        "Syke DB",
        has_syke_db,
        str(syke_db_path) if has_syke_db else "not found — run 'syke setup'",
    )
    _print_check(
        "Events DB",
        has_events_db,
        str(events_db_path) if has_events_db else "not found — created on first run",
    )

    # Daemon — prefer launchd status (macOS one-shot), fall back to PID check
    daemon_running, pid = is_running()
    launchd_out = launchd_status()
    if launchd_out is not None:
        import re

        daemon_ok = True
        if daemon_running and pid is not None:
            detail = f"launchd registered, PID {pid}"
        else:
            m = re.search(r'"LastExitStatus"\s*=\s*(\d+)', launchd_out)
            exit_status = m.group(1) if m else "?"
            detail = f"launchd registered (last exit: {exit_status})"
    else:
        daemon_ok = daemon_running
        if daemon_running and pid is not None:
            detail = f"PID {pid}"
        else:
            detail = "not running — run 'syke daemon start'"
    _print_check("Daemon", daemon_ok, detail)

    if has_db:
        db = get_db(user_id)
        try:
            count = db.count_events(user_id)
            console.print(f"  Events: {count}")

            from syke.health import (
                evolution_trends as _evo_trends,
            )
            from syke.health import (
                memex_health as _memex_h,
            )
            from syke.health import (
                memory_health as _mem_h,
            )
            from syke.health import (
                synthesis_health as _syn_h,
            )

            console.print("\n  [bold]Memory Health[/bold]")

            mh = _mem_h(db, user_id)
            _print_check(
                "Graph",
                mh["assessment"] in ("healthy", "dense"),
                f"{mh['active']} active, {mh['links']} links, "
                f"{mh['orphan_pct']}% orphaned ({mh['assessment']})",
            )

            sh = _syn_h(db, user_id)
            _print_check(
                "Synthesis",
                sh["assessment"] in ("active", "recent"),
                f"{sh['last_run_ago']} ({sh['assessment']})",
            )

            mx = _memex_h(db, user_id)
            _print_check(
                "Memex",
                mx["assessment"] in ("fresh", "healthy", "ok"),
                f"{mx['lines']} lines, updated {mx['updated_ago']} ({mx['assessment']})",
            )

            ev = _evo_trends(db, user_id)
            _print_check(
                f"Evolution ({ev['days']}d)",
                ev["assessment"] != "dormant",
                f"+{ev['created']} created, -{ev['superseded']} superseded ({ev['assessment']})",
            )
        finally:
            db.close()

    # Harness adapters
    from syke.distribution.harness import status_all

    statuses = status_all()
    if statuses:
        console.print("\n  [bold]Harness Adapters[/bold]")
        for s in statuses:
            if s.detected and s.connected:
                tag = "[green]connected[/green]"
            elif s.detected:
                tag = "[yellow]detected[/yellow]"
            else:
                tag = "[dim]not found[/dim]"
            extra = f"  ({s.notes})" if s.notes else ""
            _print_check(s.name, s.connected, f"{tag}{extra}")

    # Network probe (optional)
    if network:
        console.print("\n  [bold]Network Probe[/bold]")
        _run_network_probe(ctx)


def _resolve_source(cli_provider: str | None) -> str:
    if cli_provider:
        return "CLI --provider flag"
    if os.getenv("SYKE_PROVIDER"):
        return "SYKE_PROVIDER env"
    from syke.llm import AuthStore

    store = AuthStore()
    if store.get_active_provider():
        return "auth.json"
    return "unknown"


def _run_network_probe(ctx: click.Context) -> None:
    from syke.llm.env import build_pi_runtime_env, resolve_provider

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
    except (ValueError, RuntimeError) as e:
        _print_check("Network", False, f"Cannot resolve provider: {e}")
        return

    try:
        env = build_pi_runtime_env(provider)
    except RuntimeError as e:
        _print_check("Network", False, str(e))
        return

    visible_creds = [name for name, value in env.items() if name.endswith("_API_KEY") and value]
    visible_urls = [name for name, value in env.items() if name.endswith("_BASE_URL") and value]
    detail = "Pi-native provider env prepared"
    if visible_creds:
        detail += f" | creds: {', '.join(sorted(visible_creds))}"
    if visible_urls:
        detail += f" | urls: {', '.join(sorted(visible_urls))}"
    _print_check("Network", True, detail)
    console.print("         Pi-native HTTP probing is not implemented yet; use `syke ask` as the live check.")
    return


@cli.command()
@click.argument("path")
@click.pass_context
def connect(ctx: click.Context, path: str) -> None:
    """Connect a new AI harness to Syke."""
    from syke.config import user_data_dir
    from syke.llm.simple import build_llm_fn
    from syke.observe.factory import connect as factory_connect

    user_id = ctx.obj["user"]
    adapters_dir = user_data_dir(user_id) / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)

    try:
        llm_fn = build_llm_fn()
    except Exception as exc:
        console.print(f"[yellow]LLM unavailable ({exc}), using template generator[/yellow]")
        llm_fn = None

    success, message = factory_connect(path, llm_fn=llm_fn, adapters_dir=adapters_dir)
    if success:
        console.print(f"[green]✓[/green] Connected: {message}")
    else:
        console.print(f"[red]✗[/red] Failed: {message}")
        ctx.exit(1)


@cli.group()
@click.pass_context
def dev(ctx: click.Context) -> None:
    """Developer helpers for Syke runtime packaging."""
    if ctx.invoked_subcommand is None:
        console.print("[bold]Dev helpers[/bold]")
        console.print("  install-safe  Build the non-editable tool install used by launchd.")


@dev.command("install-safe")
@click.pass_context
def dev_install_safe(ctx: click.Context) -> None:
    """Build/install the safe current-branch tool used by launchd."""
    _run_managed_checkout_install(
        user_id=ctx.obj["user"],
        installer="uv",
        restart_daemon=True,
        prompt=False,
    )


@sense.command("discover")
@click.pass_context
def sense_discover(ctx: click.Context) -> None:
    """Discover AI harnesses on this machine."""
    from syke.observe.factory import discover

    console.print("[bold]Discovering AI harnesses...[/bold]")
    results = discover()
    if not results:
        console.print("[dim]No known harnesses found.[/dim]")
        return

    table = Table(title="Discovered Harnesses")
    table.add_column("Source", style="cyan")
    table.add_column("Path", style="dim")
    table.add_column("Format", style="green")

    for result in results:
        table.add_row(
            result["source"],
            str(result["path"]),
            result["format"],
        )
    console.print(table)
