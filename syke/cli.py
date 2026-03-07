"""Click CLI for Syke."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from syke import __version__
from syke.config import DEFAULT_USER, _is_source_install, user_db_path
from syke.db import SykeDB
from syke.time import format_for_human

console = Console()


def get_db(user_id: str) -> SykeDB:
    """Get an initialized DB for a user."""
    return SykeDB(user_db_path(user_id))


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
    """Show status of ingested data and profiles."""
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

        # Show memex stats instead of profile
        memex = db.get_memex(user_id)
        if memex:
            mem_count = db.count_memories(user_id)
            created = memex.get("created_at", "unknown")
            console.print(f"\n[bold]Memex[/bold]: synthesized at {created} ({mem_count} memories)")
        else:
            console.print("\n[dim]No memex yet. Run: syke setup --user <name>[/dim]")
    finally:
        db.close()


@cli.group(hidden=True)
def ingest() -> None:
    """Ingest data from platforms."""
    pass


@ingest.command("claude-code")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_claude_code(ctx: click.Context, yes: bool) -> None:
    """Ingest Claude Code session transcripts from ~/.claude/."""
    from syke.ingestion.claude_code import ClaudeCodeAdapter
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]

    if not yes:
        console.print(
            "\n[bold yellow]This will read your Claude Code session transcripts[/bold yellow]"
            "\nfrom [cyan]~/.claude/transcripts/[/cyan]"
            "\n\nThis includes your private conversations with Claude."
            "\nData stays local in [cyan]data/{user}/syke.db[/cyan] — never uploaded.\n"
        )
        if not click.confirm("Proceed with ingestion?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        with tracker.track("ingest_claude_code") as metrics:
            adapter = ClaudeCodeAdapter(db, user_id)
            result = adapter.ingest()
            metrics.events_processed = result.events_count
        console.print(
            f"[green]Claude Code ingestion complete:[/green] {result.events_count} sessions"
        )
    finally:
        db.close()


@ingest.command("gmail")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.option(
    "--account",
    default=None,
    help="Gmail address (for gog backend; default: GMAIL_ACCOUNT env)",
)
@click.option("--max-results", default=200, help="Max emails to fetch (default: 200)")
@click.option("--days", default=30, help="Days to look back on first run (default: 30)")
@click.option("--query", default=None, help="Custom Gmail search query (overrides auto-filter)")
@click.pass_context
def ingest_gmail(
    ctx: click.Context,
    yes: bool,
    account: str | None,
    max_results: int,
    days: int,
    query: str | None,
) -> None:
    """Ingest emails from Gmail.

    Automatically picks the best backend: gog CLI if installed + authenticated,
    otherwise Python OAuth (google-auth-oauthlib).
    """
    from syke.ingestion.gmail import GmailAdapter
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]

    if not yes:
        console.print(
            "\n[bold yellow]This will read your Gmail inbox[/bold yellow]"
            "\n(subjects, snippets, bodies, labels, sent patterns)."
            "\nData stays local — never uploaded.\n"
        )
        if not click.confirm("Proceed with ingestion?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    kwargs: dict[str, str | int] = {"max_results": max_results, "days": days}
    if account:
        kwargs["account"] = account
    if query:
        kwargs["query"] = query
    try:
        with tracker.track("ingest_gmail") as metrics:
            adapter = GmailAdapter(db, user_id)
            result = adapter.ingest(**kwargs)
            metrics.events_processed = result.events_count
        console.print(f"[green]Gmail ingestion complete:[/green] {result.events_count} events")
    finally:
        db.close()


@ingest.command("chatgpt")
@click.option("--file", "-f", "file_path", required=True, type=click.Path(exists=True))
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_chatgpt(ctx: click.Context, file_path: str, yes: bool) -> None:
    """Ingest ChatGPT export ZIP file."""
    from syke.ingestion.chatgpt import ChatGPTAdapter
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


@ingest.command("github")
@click.option("--username", required=True, help="GitHub username")
@click.pass_context
def ingest_github(ctx: click.Context, username: str) -> None:
    """Ingest data from GitHub API."""
    from syke.ingestion.github_ import GitHubAdapter
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        with tracker.track("ingest_github", username=username) as metrics:
            adapter = GitHubAdapter(db, user_id)
            result = adapter.ingest(username=username)
            metrics.events_processed = result.events_count
        console.print(f"[green]GitHub ingestion complete:[/green] {result.events_count} events")
    finally:
        db.close()


@ingest.command("codex")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompt")
@click.pass_context
def ingest_codex(ctx: click.Context, yes: bool) -> None:
    """Ingest Codex CLI session transcripts from ~/.codex/."""
    from syke.ingestion.codex import CodexAdapter
    from syke.metrics import MetricsTracker

    user_id = ctx.obj["user"]

    if not yes:
        console.print(
            "\n[bold yellow]This will read your Codex session transcripts[/bold yellow]"
            "\nfrom [cyan]~/.codex/sessions/[/cyan]"
            "\n\nThis includes your private Codex conversations."
            "\nData stays local in [cyan]data/{user}/syke.db[/cyan] — never uploaded.\n"
        )
        if not click.confirm("Proceed with ingestion?"):
            console.print("[dim]Cancelled.[/dim]")
            return

    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        with tracker.track("ingest_codex") as metrics:
            adapter = CodexAdapter(db, user_id)
            result = adapter.ingest()
            metrics.events_processed = result.events_count
        console.print(f"[green]Codex ingestion complete:[/green] {result.events_count} sessions")
    finally:
        db.close()


@ingest.command("all")
@click.option("--yes", "-y", is_flag=True, help="Skip consent prompts for private sources")
@click.pass_context
def ingest_all(ctx: click.Context, yes: bool) -> None:
    """Ingest from all available sources."""
    console.print("[bold]Ingesting from all sources...[/bold]\n")

    # Claude Code (private — needs consent)
    try:
        ctx.invoke(ingest_claude_code, yes=yes)
    except (SystemExit, Exception) as e:
        console.print(f"  [yellow]claude-code skipped:[/yellow] {e}")

    # GitHub (public — no consent needed)
    import subprocess

    gh_username = None
    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            gh_username = r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if gh_username:
        try:
            ctx.invoke(ingest_github, username=gh_username)
        except (SystemExit, Exception) as e:
            console.print(f"  [yellow]github skipped:[/yellow] {e}")
    else:
        console.print(
            "  [dim]github: no username detected (install gh CLI or set GITHUB_TOKEN)[/dim]"
        )

    # Gmail (private — needs consent)
    try:
        ctx.invoke(ingest_gmail, yes=yes)
    except (SystemExit, Exception) as e:
        console.print(f"  [yellow]gmail skipped:[/yellow] {e}")

    console.print("\n[bold]All sources processed.[/bold]")


def _claude_is_authenticated() -> bool:
    import shutil

    claude_dir = Path.home() / ".claude"

    if not shutil.which("claude"):
        return False
    if not claude_dir.is_dir():
        return False

    return any(claude_dir.glob("*.json"))


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


@cli.command(hidden=True)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["json", "markdown", "claude-md", "user-md"]),
    default="json",
    help="Output format",
)
@click.option("--output", "-o", type=click.Path(), help="Output file path")
@click.pass_context
def profile(ctx: click.Context, fmt: str, output: str | None) -> None:
    """Output the latest user profile."""
    from syke.distribution.formatters import format_profile

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        prof = db.get_latest_profile(user_id)
        if not prof:
            console.print("[red]No profile found. Run: syke setup --user <name>[/red]")
            sys.exit(1)

        text = format_profile(prof, fmt)

        if output:
            Path(output).write_text(text)
            console.print(f"[green]Profile written to {output}[/green]")
        else:
            console.print(text)
    finally:
        db.close()


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
            "github": "green",
            "chatgpt": "yellow",
            "gmail": "blue",
            "opencode": "magenta",
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

    from syke.distribution.ask_agent import AskEvent
    from syke.distribution.ask_agent import ask_stream as run_ask_stream
    from syke.llm.env import resolve_provider

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
        provider_label = provider.id
    except Exception:
        provider_label = "unknown"

    # Emit early stdout byte so callers (e.g. Claude Code Bash tool)
    # see output before the SDK thinking phase completes (~3-7s).
    _sys.stdout.write(" \n")
    _sys.stdout.flush()

    # SIGTERM handler: dump local fallback before dying.
    _sigterm_fired = False

    def _on_sigterm(signum, frame):
        nonlocal _sigterm_fired
        _sigterm_fired = True
        from syke.distribution.ask_agent import _local_fallback

        _sys.stdout.write(_local_fallback(db, user_id, question) + "\n")
        _sys.stdout.flush()
        raise SystemExit(143)

    prev_handler = _signal.signal(_signal.SIGTERM, _on_sigterm)

    try:
        # Mute the console log handler during streaming to prevent
        # [metrics] lines from interleaving with the streamed output.
        syke_logger = _logging.getLogger("syke")
        saved_levels = {
            h: h.level
            for h in syke_logger.handlers
            if isinstance(h, _logging.StreamHandler) and not isinstance(h, _logging.FileHandler)
        }
        for h in saved_levels:
            h.setLevel(_logging.CRITICAL)

        has_text = False
        has_thinking = False

        def _on_event(event: AskEvent) -> None:
            nonlocal has_text, has_thinking
            if event.type == "thinking":
                # Show thinking in dim italic on stderr so it doesn't
                # pollute stdout if the user pipes the answer.
                if not has_thinking:
                    _sys.stderr.write("\033[2;3m")  # dim + italic
                    has_thinking = True
                _sys.stderr.write(event.content)
                _sys.stderr.flush()
            elif event.type == "text":
                if has_thinking:
                    _sys.stderr.write("\033[0m\n")  # reset + newline
                    _sys.stderr.flush()
                    has_thinking = False
                has_text = True
                _sys.stdout.write(event.content)
                _sys.stdout.flush()
            elif event.type == "tool_call":
                if has_thinking:
                    _sys.stderr.write("\033[0m\n")
                    _sys.stderr.flush()
                    has_thinking = False
                # Show tool invocation in dim on stderr
                preview = ""
                inp = event.metadata and event.metadata.get("input")
                if isinstance(inp, dict):
                    for v in inp.values():
                        if isinstance(v, str) and v:
                            preview = v[:60]
                            break
                # Strip SDK prefix: mcp__syke__search_memories → search_memories
                tool_name = event.content.removeprefix("mcp__syke__")
                label = f"  \u21b3 {tool_name}({preview})"
                _sys.stderr.write(f"\033[2m{label}\033[0m\n")
                _sys.stderr.flush()

        try:
            answer, cost = run_ask_stream(db, user_id, question, _on_event)
        finally:
            # Reset ANSI state if we were mid-thinking
            if has_thinking:
                _sys.stderr.write("\033[0m\n")
                _sys.stderr.flush()
            for h, lvl in saved_levels.items():
                h.setLevel(lvl)

        # Newline after streamed text, or fallback print if SDK produced no stream events
        if has_text:
            _sys.stdout.write("\n")
            _sys.stdout.flush()
        elif answer and answer.strip():
            console.print(f"\n{answer}")

        if cost:
            secs = cost.get("duration_seconds", 0)
            usd = cost.get("cost_usd", 0)
            tokens = int(cost.get("tokens", 0))
            _sys.stderr.write(
                f"\033[2m{provider_label} \u00b7 {secs:.1f}s \u00b7 ${usd:.4f} \u00b7 {tokens} tokens\033[0m\n"
            )
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
    from syke.ingestion.gateway import IngestGateway

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

            result = gw.push(
                source=ev.get("source", source),
                event_type=ev.get("event_type", "observation"),
                title=ev.get("title", ""),
                content=ev.get("text", ev.get("content", "")),
                timestamp=ev.get("timestamp"),
                metadata={"tags": ev.get("tags", list(tag))} if ev.get("tags") or tag else None,
                external_id=ev.get("external_id"),
            )
            if result["status"] == "ok":
                console.print(f"Recorded. [dim]({result['event_id'][:8]})[/dim]")
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

        metadata = {"tags": list(tag)} if tag else None

        result = gw.push(
            source=source,
            event_type="observation",
            title=title or "",
            content=content,
            metadata=metadata,
        )

        if result["status"] == "ok":
            console.print(f"Recorded. [dim]({result['event_id'][:8]})[/dim]")
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

    # GitHub (check for token or public access)
    gh_token = _os.getenv("GITHUB_TOKEN", "")
    if gh_token:
        sources.append(("github", "API token configured", "GITHUB_TOKEN env"))
        console.print("  [green]FOUND[/green]  github         API token configured")
    else:
        # Try gh CLI
        import subprocess

        try:
            result = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                sources.append(("github", "gh CLI authenticated", "gh auth"))
                console.print("  [green]FOUND[/green]  github         gh CLI authenticated")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            console.print("  [dim]SKIP[/dim]   github         No token or gh CLI found")

    # Gmail — check gog CLI first, then Python OAuth
    from syke.ingestion.gmail import _gog_authenticated, _python_oauth_available

    _gmail_account = _os.getenv("GMAIL_ACCOUNT", "")
    _gmail_found = False

    if _gmail_account and _gog_authenticated(_gmail_account):
        sources.append(("gmail", f"gog CLI ({_gmail_account})", "gog auth"))
        console.print(
            f"  [green]FOUND[/green]  gmail          gog CLI authenticated ({_gmail_account})"
        )
        _gmail_found = True
    elif _python_oauth_available():
        _token_path = _Path(
            _os.path.expanduser(_os.getenv("GMAIL_TOKEN_PATH", "~/.config/syke/gmail_token.json"))
        )
        if _token_path.exists():
            sources.append(("gmail", "Python OAuth (token cached)", str(_token_path)))
            console.print("  [green]FOUND[/green]  gmail          Python OAuth token cached")
            _gmail_found = True
        else:
            _creds_path = _Path(
                _os.path.expanduser(
                    _os.getenv(
                        "GMAIL_CREDENTIALS_PATH",
                        "~/.config/syke/gmail_credentials.json",
                    )
                )
            )
            if _creds_path.exists():
                sources.append(("gmail", "Python OAuth (credentials ready)", str(_creds_path)))
                console.print(
                    "  [green]FOUND[/green]  gmail          Python OAuth credentials ready (will prompt for consent)"
                )
                _gmail_found = True

    if not _gmail_found:
        if _python_oauth_available():
            console.print(
                "  [yellow]READY[/yellow]  gmail          Python OAuth installed — needs credentials\n"
                "              [dim]Download from https://console.cloud.google.com/apis/credentials[/dim]\n"
                "              [dim]Save to: ~/.config/syke/gmail_credentials.json[/dim]"
            )
        else:
            console.print(
                "  [dim]SKIP[/dim]   gmail          No backend available\n"
                "              [dim]Option A: brew install gog && gog auth add --account you@gmail.com[/dim]\n"
                "              [dim]Option B: pip install google-auth-oauthlib google-api-python-client[/dim]"
            )

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
    from syke.llm.env import _claude_login_available

    store = AuthStore()
    current_active = store.get_active_provider()

    # Discover all providers and their readiness
    # (id, label, ready) — ready means credentials exist and provider is usable now
    providers: list[tuple[str, str, bool]] = []

    has_claude = _claude_login_available()
    providers.append(
        (
            "claude-login",
            "Claude Code session auth" if has_claude else "Claude Code — run 'claude login' first",
            has_claude,
        )
    )

    has_codex = read_codex_auth() is not None
    providers.append(
        (
            "codex",
            "ChatGPT Plus via Codex" if has_codex else "Codex — run 'codex login' first",
            has_codex,
        )
    )

    for pid, name in [("openrouter", "OpenRouter"), ("zai", "z.ai")]:
        has_key = store.get_token(pid) is not None
        providers.append(
            (
                pid,
                name if has_key else f"{name} — enter API key",
                has_key,
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

    # Pre-select: current active > first ready > first entry
    default_idx = 0
    if current_active:
        for i, (pid, _, _) in enumerate(providers):
            if pid == current_active:
                default_idx = i
                break
    else:
        for i, (_, _, ready) in enumerate(providers):
            if ready:
                default_idx = i
                break

    idx = _term_menu_select(entries, title="\n  Select a provider:\n", default_index=default_idx)

    if idx is None or idx == len(entries) - 1:
        return False

    selected_pid, _, is_ready = providers[idx]

    if not is_ready:
        if selected_pid in ("claude-login", "codex"):
            cmd = "claude login" if selected_pid == "claude-login" else "codex login"
            console.print(f"\n  Run [bold]{cmd}[/bold] and then re-run [bold]syke setup[/bold].")
            return False
        else:
            return _setup_api_key_flow(selected_pid)

    store.set_active_provider(selected_pid)
    console.print(f"\n  [green]✓[/green]  Provider: [bold]{selected_pid}[/bold]")
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
@click.option("--yes", "-y", is_flag=True, help="Auto-consent to all private sources")
@click.option("--skip-daemon", is_flag=True, help="Skip LaunchAgent daemon install")
@click.pass_context
def setup(ctx: click.Context, yes: bool, skip_daemon: bool) -> None:
    """Full automated setup: detect sources, collect data, build profile.

    Designed for agent-driven installation. An AI agent can run:
        syke setup --user <name> --yes
    to go from zero to a complete identity profile.
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

        # Claude Code (always check — if running in Claude Code, this is guaranteed)
        claude_dir = _Path(_os.path.expanduser("~/.claude"))
        if (claude_dir / "transcripts").exists() or (claude_dir / "projects").exists():
            console.print("  [cyan]Ingesting Claude Code sessions...[/cyan]")
            from syke.ingestion.claude_code import ClaudeCodeAdapter
            from syke.metrics import MetricsTracker

            tracker = MetricsTracker(user_id)
            with tracker.track("ingest_claude_code") as metrics:
                adapter = ClaudeCodeAdapter(db, user_id)
                result = adapter.ingest()
                metrics.events_processed = result.events_count
            console.print(f"  [green]OK[/green]  Claude Code: {result.events_count} sessions")
            ingested_count += result.events_count

        # Codex CLI sessions
        codex_dir = _Path(_os.path.expanduser("~/.codex"))
        if (codex_dir / "sessions").exists() or (codex_dir / "history.jsonl").exists():
            console.print("  [cyan]Ingesting Codex sessions...[/cyan]")
            from syke.ingestion.codex import CodexAdapter
            from syke.metrics import MetricsTracker

            tracker = MetricsTracker(user_id)
            with tracker.track("ingest_codex") as metrics:
                adapter = CodexAdapter(db, user_id)
                result = adapter.ingest()
                metrics.events_processed = result.events_count
            console.print(f"  [green]OK[/green]  Codex: {result.events_count} sessions")
            ingested_count += result.events_count

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
            from syke.ingestion.chatgpt import ChatGPTAdapter
            from syke.metrics import MetricsTracker

            tracker = MetricsTracker(user_id)
            with tracker.track("ingest_chatgpt") as metrics:
                adapter = ChatGPTAdapter(db, user_id)
                result = adapter.ingest(file_path=str(chatgpt_zip))
                metrics.events_processed = result.events_count
            console.print(f"  [green]OK[/green]  ChatGPT: {result.events_count} conversations")
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
            try:
                console.print(f"  [cyan]Ingesting GitHub...[/cyan] (@{gh_username})")
                from syke.ingestion.github_ import GitHubAdapter
                from syke.metrics import MetricsTracker

                tracker = MetricsTracker(user_id)
                with tracker.track("ingest_github") as metrics:
                    adapter = GitHubAdapter(db, user_id)
                    result = adapter.ingest(username=gh_username)
                    metrics.events_processed = result.events_count
                console.print(f"  [green]OK[/green]  GitHub: {result.events_count} events")
                ingested_count += result.events_count
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  GitHub: {e}")

        # Check total events in DB (including previously ingested)
        total_in_db = db.count_events(user_id)
        if ingested_count == 0 and total_in_db == 0:
            console.print("[yellow]No data sources found to ingest.[/yellow]")
            return
        if ingested_count == 0 and total_in_db > 0:
            console.print(f"  [dim]No new events ({total_in_db} already collected)[/dim]")
            ingested_count = total_in_db

        # Step 3: Background daemon (synthesis runs on first tick)
        if not skip_daemon:
            console.print("\n[bold]Step 3:[/bold] Background sync daemon\n")
            try:
                if yes:
                    ctx.invoke(start, interval=900)
                    console.print("  [green]OK[/green]  Daemon started. Syncs every 15 minutes.")
                else:
                    from rich.prompt import Confirm

                    if Confirm.ask("Install background sync daemon? (recommended)", default=True):
                        ctx.invoke(start, interval=900)
                        console.print(
                            "  [green]OK[/green]  Daemon started. Syncs every 15 minutes."
                        )
                    else:
                        console.print(
                            "  [dim]Skipped daemon install."
                            " You can install later with: syke daemon start[/dim]"
                        )
            except Exception as e:
                console.print(f"  [yellow]SKIP[/yellow]  Daemon install failed: {e}")
                console.print("  [dim]You can install manually with: syke daemon start[/dim]")

        # Final summary
        console.print("\n[bold green]Setup complete.[/bold green]")
        console.print(f"  {ingested_count} events collected")

        if not skip_daemon:
            from syke.daemon.daemon import is_running

            running, _ = is_running()
            if running and has_provider:
                console.print("  Daemon started — synthesis will run in the background.")
                console.print("  Run [bold]syke context[/bold] in a few minutes to see your memex.")
            elif running:
                console.print("  Daemon started. Configure a provider to enable synthesis.")
                console.print("  [dim]syke auth set <provider> --api-key <key>[/dim]")
            else:
                console.print("  Run [bold]syke daemon start[/bold] to enable background sync.")
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
    """Sync new data and update profile.

    Pulls new events from all connected sources, then runs an incremental
    profile update if enough new data is found (minimum 5 events).
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
    from syke.llm.env import _claude_login_available

    store = AuthStore()
    active = store.get_active_provider()
    stored = store.list_providers()

    if not active and _claude_login_available():
        active = "claude-login"
        source = "auto-detected"
    elif active:
        source = "auth.json"
    else:
        source = None

    if active:
        spec = PROVIDERS.get(active)
        console.print(f"[bold]Active provider:[/bold] {active} [dim]({source})[/dim]")
        if spec and spec.base_url:
            console.print(f"  Base URL: {spec.base_url}")
    else:
        console.print(
            "[yellow]No provider configured.[/yellow] Run [bold]syke auth set <provider> --api-key <key>[/bold]"
            " or [bold]claude login[/bold]."
        )

    if stored:
        console.print("\n[bold]Configured:[/bold]")
        for pid, info in stored.items():
            marker = " [green]← active[/green]" if info["active"] else ""
            console.print(f"  {pid}: {info['credential']}{marker}")

    unconfigured = [pid for pid in sorted(PROVIDERS) if pid not in stored and pid != "claude-login"]
    if unconfigured:
        console.print(f"\n[dim]Available: {', '.join(unconfigured)}[/dim]")


@auth.command("set")
@click.argument("provider")
@click.option(
    "--api-key",
    required=True,
    prompt=True,
    hide_input=True,
    help="API key / auth token",
)
@click.pass_context
def auth_set(ctx: click.Context, provider: str, api_key: str) -> None:
    """Store credentials for a provider and activate it."""
    from syke.llm import PROVIDERS, AuthStore

    if provider not in PROVIDERS:
        valid = ", ".join(sorted(PROVIDERS))
        console.print(f"[red]Unknown provider '{provider}'. Valid: {valid}[/red]")
        raise SystemExit(1)

    spec = PROVIDERS[provider]
    if spec.is_claude_login:
        console.print("[yellow]claude-login uses 'claude login' — no API key needed.[/yellow]")
        raise SystemExit(1)

    store = AuthStore()
    store.set_token(provider, api_key)
    store.set_active_provider(provider)
    console.print(
        f"[green]✓[/green] Credentials stored and [bold]{provider}[/bold] set as active provider."
    )


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

    if spec.needs_proxy:
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
    elif not spec.is_claude_login:
        token = store.get_token(provider)
        if not token:
            console.print(
                f"[yellow]No credentials for {provider}.[/yellow]"
                f" Run [bold]syke auth set {provider} --api-key <key>[/bold] first."
            )
            raise SystemExit(1)
        store.set_active_provider(provider)
        console.print(f"[green]\u2713[/green] Active provider set to [bold]{provider}[/bold].")
    else:
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
# syke daemon — background sync
# ---------------------------------------------------------------------------


@cli.group()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Background sync daemon (start, stop, status, logs)."""
    pass


@daemon.command()
@click.option(
    "--interval",
    type=int,
    default=900,
    help="Sync interval in seconds (default: 900 = 15 min)",
)
@click.pass_context
def start(ctx: click.Context, interval: int) -> None:
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


@daemon.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
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
    db_path = user_db_path(user_id)
    if db_path.exists():
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
# syke doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--network", is_flag=True, help="Test real API connectivity")
@click.pass_context
def doctor(ctx: click.Context, network: bool) -> None:
    """Verify Syke installation health."""
    import shutil

    from syke.daemon.daemon import is_running, launchd_status

    user_id = ctx.obj["user"]
    console.print(f"[bold]Syke Doctor[/bold]  ·  user: {user_id}\n")

    # Provider resolution
    from syke.llm.auth_store import _redact
    from syke.llm.env import build_agent_env, resolve_provider

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
        source = _resolve_source(ctx.obj.get("provider"))
        _print_check("Provider", True, f"{provider.id} (source: {source})")
        if provider.base_url:
            console.print(f"         Base URL: {provider.base_url}")

        env = build_agent_env(provider)
        token = env.get("ANTHROPIC_AUTH_TOKEN")
        if token:
            console.print(f"         Credential: {_redact(token)}")
        elif provider.is_claude_login:
            console.print("         Credential: claude login (local auth files)")
    except (ValueError, RuntimeError) as e:
        _print_check("Provider", False, str(e))

    # Claude binary
    has_binary = bool(shutil.which("claude"))
    _print_check(
        "Claude binary",
        has_binary,
        "in PATH" if has_binary else "not found — install Claude Code",
    )

    # Claude auth (still useful even with other providers — shows local state)
    has_auth = _claude_is_authenticated()
    _print_check(
        "Claude auth",
        has_auth,
        "~/.claude/ has tokens"
        if has_auth
        else "not found (optional — only needed for claude-login provider)",
    )

    # Database
    db_path = user_db_path(user_id)
    has_db = db_path.exists()
    _print_check("Database", has_db, str(db_path) if has_db else "not found — run 'syke setup'")

    # Daemon — prefer launchd status (macOS one-shot), fall back to PID check
    launchd_out = launchd_status()
    if launchd_out is not None:
        import re

        m = re.search(r'"LastExitStatus"\s*=\s*(\d+)', launchd_out)
        exit_status = m.group(1) if m else "?"
        daemon_ok = True
        detail = f"launchd registered (last exit: {exit_status})"
    else:
        daemon_ok, pid = is_running()
        if daemon_ok:
            detail = f"PID {pid}"
        else:
            detail = "not running — run 'syke daemon start'"
    _print_check("Daemon", daemon_ok, detail)

    if has_db:
        db = get_db(user_id)
        try:
            count = db.count_events(user_id)
            console.print(f"  Events: {count}")
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
    from syke.llm.env import _claude_login_available

    store = AuthStore()
    if store.get_active_provider():
        return "auth.json"
    if _claude_login_available():
        return "auto-detected"
    return "unknown"


def _run_network_probe(ctx: click.Context) -> None:
    import time

    from syke.llm.env import build_agent_env, resolve_provider

    try:
        provider = resolve_provider(cli_provider=ctx.obj.get("provider"))
    except (ValueError, RuntimeError) as e:
        _print_check("Network", False, f"Cannot resolve provider: {e}")
        return

    try:
        env = build_agent_env(provider)
    except RuntimeError as e:
        _print_check("Network", False, str(e))
        return

    base_url = env.get("ANTHROPIC_BASE_URL")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")

    if provider.is_claude_login:
        _print_check("Network", True, "claude-login — use 'claude login' to verify auth")
        return

    if not token or token in ("", "codex-proxy"):
        if not provider.needs_proxy:
            _print_check("Network", False, "No auth token configured for this provider")
            return

    try:
        import httpx

        url = f"{base_url}/v1/messages" if base_url else "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }

        t0 = time.monotonic()
        resp = httpx.post(url, headers=headers, json=body, timeout=30)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code == 200:
            data = resp.json()
            model = data.get("model", "unknown")
            _print_check("Network", True, f"PASS ({elapsed_ms}ms, model: {model})")
        else:
            detail = resp.text[:200] if resp.text else str(resp.status_code)
            _print_check("Network", False, f"HTTP {resp.status_code}: {detail}")
    except ImportError:
        _print_check("Network", False, "httpx not installed — run 'pip install httpx'")
    except Exception as e:
        _print_check("Network", False, str(e))
    finally:
        if provider.needs_proxy:
            from syke.llm.codex_proxy import stop_codex_proxy

            stop_codex_proxy()


# Register experiment commands if available (untracked)
try:
    from experiments.cli_experiments import register_experiment_commands

    register_experiment_commands(cli)
except ImportError:
    pass
