"""Click CLI for Syke."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from syke import __version__
from syke.config import DEFAULT_USER, _is_source_install, user_data_dir, user_db_path, user_profile_path
from syke.db import SykeDB

console = Console()


def get_db(user_id: str) -> SykeDB:
    """Get an initialized DB for a user."""
    return SykeDB(user_db_path(user_id))


@click.group()
@click.option("--user", "-u", default=DEFAULT_USER, help="User ID")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.version_option(__version__)
@click.pass_context
def cli(ctx: click.Context, user: str, verbose: bool) -> None:
    """Syke — Personal context daemon."""
    ctx.ensure_object(dict)
    ctx.obj["user"] = user
    ctx.obj["verbose"] = verbose

    from syke.metrics import setup_logging
    setup_logging(user, verbose=verbose)


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

        if info["latest_profile"]:
            p = info["latest_profile"]
            console.print(
                f"\n[bold]Latest Profile[/bold]: {p['created_at']} "
                f"({p['events_count']} events, {p['sources']})"
            )
        else:
            console.print(
                "\n[dim]No profile yet. Run: syke setup --user <name>[/dim]"
            )
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
@click.option("--account", default=None, help="Gmail address (for gog backend; default: GMAIL_ACCOUNT env)")
@click.option("--max-results", default=200, help="Max emails to fetch (default: 200)")
@click.option("--days", default=30, help="Days to look back on first run (default: 30)")
@click.option("--query", default=None, help="Custom Gmail search query (overrides auto-filter)")
@click.pass_context
def ingest_gmail(ctx: click.Context, yes: bool, account: str | None, max_results: int, days: int, query: str | None) -> None:
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
    kwargs: dict = {"max_results": max_results, "days": days}
    if account:
        kwargs["account"] = account
    if query:
        kwargs["query"] = query
    try:
        with tracker.track("ingest_gmail") as metrics:
            adapter = GmailAdapter(db, user_id)
            result = adapter.ingest(**kwargs)
            metrics.events_processed = result.events_count
        console.print(
            f"[green]Gmail ingestion complete:[/green] {result.events_count} events"
        )
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
        console.print(
            f"[green]ChatGPT ingestion complete:[/green] {result.events_count} events"
        )
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
        console.print(
            f"[green]GitHub ingestion complete:[/green] {result.events_count} events"
        )
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
            capture_output=True, text=True, timeout=10,
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
        console.print("  [dim]github: no username detected (install gh CLI or set GITHUB_TOKEN)[/dim]")

    # Gmail (private — needs consent)
    try:
        ctx.invoke(ingest_gmail, yes=yes)
    except (SystemExit, Exception) as e:
        console.print(f"  [yellow]gmail skipped:[/yellow] {e}")

    console.print("\n[bold]All sources processed.[/bold]")


def _claude_binary_authed() -> bool:
    """Check if claude binary is available and authenticated (claude login)."""
    import subprocess
    try:
        r = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _detect_install_method() -> str:
    """Detect how syke was installed: 'pipx' | 'pip' | 'uvx' | 'source'."""
    import shutil
    import subprocess

    if _is_source_install():
        return "source"
    try:
        r = subprocess.run(
            ["pipx", "list", "--short"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "syke" in r.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if shutil.which("syke") is None:
        return "uvx"
    return "pip"


def _make_discovery_cb(state: dict):
    """Create an on_discovery callback for agentic perception methods."""
    def on_discovery(event_type: str, detail: str) -> None:
        if event_type == "tool_call":
            state["turns"] = state.get("turns", 0) + 1
            console.print(f"  [cyan]>[/cyan] {detail}")
        elif event_type == "reasoning":
            console.print(f"  [dim]{detail}[/dim]")
        elif event_type == "result":
            state["result"] = detail
            console.print(f"  [green]{detail}[/green]")
        elif event_type == "reflection":
            console.print(f"  [magenta]REFLECT:[/magenta] {detail}")
        elif event_type == "evolution":
            console.print(f"  [bold yellow]EVOLVE:[/bold yellow] {detail}")
        elif event_type == "hook_gate":
            console.print(f"  [red]GATE:[/red] {detail}")
        elif event_type == "hook_feedback":
            console.print(f"  [yellow]HINT:[/yellow] {detail}")
        elif event_type == "hook_correction":
            console.print(f"  [yellow]CORRECTED:[/yellow] {detail}")
    return on_discovery


@cli.command(hidden=True)
@click.option("--full/--incremental", default=True, help="Full or incremental perception")
@click.option(
    "--method", "-m",
    type=click.Choice(["agentic", "agentic-v2", "meta"]),
    default=None,
    help="Perception method: agentic (default), agentic-v2 (multi-agent), meta (self-improving)",
)
@click.pass_context
def perceive(ctx: click.Context, full: bool, method: str | None) -> None:
    """Run Opus 4.6 perception on the timeline.

    By default uses agentic perception: Opus crawls the footprint with tools,
    cross-references across platforms, and submits a profile.

    Use --method agentic-v2 for multi-agent (3 Sonnet explorers + Opus synthesizer).
    """
    from syke.metrics import MetricsTracker

    if method is None:
        method = "agentic"

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    tracker = MetricsTracker(user_id)
    try:
        if method == "meta":
            # Meta-learning perception (experiment)
            from experiments.perception.meta_perceiver import MetaLearningPerceiver

            meta_state: dict = {}
            on_discovery = _make_discovery_cb(meta_state)

            mode = "full" if full else "incremental"
            console.print(f"\n[bold]Meta-Learning Perception[/bold] — mode: {mode}")
            console.print(f"  Spider building its web...\n")

            with tracker.track("perceive_meta", mode=mode) as metrics:
                perceiver = MetaLearningPerceiver(db, user_id)
                profile = perceiver.perceive(full=full, on_discovery=on_discovery, save=True)
                metrics.events_processed = profile.events_count
                metrics.cost_usd = perceiver.metrics.cost_usd
                metrics.method = "meta"
                metrics.num_turns = perceiver.metrics.num_turns
                metrics.input_tokens = perceiver.metrics.input_tokens
                metrics.output_tokens = perceiver.metrics.output_tokens
                metrics.thinking_tokens = perceiver.metrics.thinking_tokens

            profile_path = user_profile_path(user_id)
            profile_path.write_text(profile.model_dump_json(indent=2))

            from syke.distribution.formatters import format_profile
            for fmt, filename in [("claude-md", "CLAUDE.md"), ("user-md", "USER.md")]:
                (user_data_dir(user_id) / filename).write_text(format_profile(profile, fmt))

            console.print(f"\n[green]Meta-Learning Perception complete.[/green]")
            console.print(f"  Identity: {profile.identity_anchor[:120]}...")
            console.print(f"  Active threads: {len(profile.active_threads)}")
            console.print(f"  Sources: {', '.join(profile.sources)}")
            console.print(f"  Events analyzed: {profile.events_count}")
            console.print(f"  Archive: {perceiver.archive.run_count} traces")
            strategy = perceiver.archive.get_latest_strategy()
            if strategy:
                console.print(f"  Strategy: v{strategy.version}")
            console.print(f"  Turns: {perceiver.metrics.num_turns}")
            console.print(f"  Cost: ${perceiver.metrics.cost_usd:.4f}")
            console.print(f"  Saved to: {profile_path}")

        else:
            # Agentic or multi-agent perception
            use_multi = method == "agentic-v2"
            from syke.perception.agentic_perceiver import AgenticPerceiver as PerceiverCls

            agentic_state: dict = {}
            on_discovery = _make_discovery_cb(agentic_state)

            mode = "full" if full else "incremental"
            label = "Multi-Agent Perception (v2)" if use_multi else "Agentic Perception"
            console.print(f"\n[bold]{label}[/bold] — mode: {mode}")
            if use_multi:
                console.print(f"  3 Sonnet explorers + Opus synthesizer...\n")
            elif full:
                console.print(f"  Opus 4.6 is crawling the digital footprint...\n")
            else:
                console.print(f"  Sonnet is updating the profile (incremental)...\n")

            track_label = "perceive_agentic_v2" if use_multi else "perceive_agentic"
            with tracker.track(track_label, mode=mode) as metrics:
                perceiver = PerceiverCls(db, user_id, use_sub_agents=use_multi)
                profile = perceiver.perceive(full=full, on_discovery=on_discovery)
                metrics.events_processed = profile.events_count
                metrics.cost_usd = perceiver.metrics.cost_usd
                metrics.method = method
                metrics.num_turns = perceiver.metrics.num_turns
                metrics.input_tokens = perceiver.metrics.input_tokens
                metrics.output_tokens = perceiver.metrics.output_tokens
                metrics.thinking_tokens = perceiver.metrics.thinking_tokens

            profile_path = user_profile_path(user_id)
            profile_path.write_text(profile.model_dump_json(indent=2))

            from syke.distribution.formatters import format_profile
            for fmt, filename in [("claude-md", "CLAUDE.md"), ("user-md", "USER.md")]:
                (user_data_dir(user_id) / filename).write_text(format_profile(profile, fmt))

            console.print(f"\n[green]{label} complete.[/green]")
            console.print(f"  Identity: {profile.identity_anchor[:120]}...")
            console.print(f"  Active threads: {len(profile.active_threads)}")
            console.print(f"  Sources: {', '.join(profile.sources)}")
            console.print(f"  Events analyzed: {profile.events_count}")
            console.print(f"  Turns: {perceiver.metrics.num_turns}")
            console.print(f"  Input tokens: {perceiver.metrics.input_tokens:,}")
            console.print(f"  Output tokens: {perceiver.metrics.output_tokens:,}")
            console.print(f"  Thinking tokens: {perceiver.metrics.thinking_tokens:,}")
            console.print(f"  Cost: ${perceiver.metrics.cost_usd:.4f}")
            console.print(f"  Saved to: {profile_path}")
    finally:
        db.close()


@cli.command(hidden=True)
@click.option(
    "--format", "-f", "fmt",
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
    "--format", "-f", "fmt",
    type=click.Choice(["claude-md", "user-md"]),
    default="claude-md",
)
@click.pass_context
def inject(ctx: click.Context, target: str, fmt: str) -> None:
    """Inject profile into a target directory."""
    from syke.distribution.inject import inject_profile

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        prof = db.get_latest_profile(user_id)
        if not prof:
            console.print("[red]No profile found. Run: syke setup --user <name>[/red]")
            sys.exit(1)

        path = inject_profile(prof, target, fmt)
        console.print(f"[green]Profile injected to {path}[/green]")
    finally:
        db.close()


@cli.command(hidden=True)
@click.option("--since", default=None, help="ISO date to filter from")
@click.option("--limit", "-n", default=50, help="Max events to show")
@click.option("--source", "-s", default=None, help="Filter by source")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "json"]), default="table", help="Output format")
@click.pass_context
def timeline(ctx: click.Context, since: str | None, limit: int, source: str | None, fmt: str) -> None:
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

        table = Table(title=f"Timeline — {user_id}")
        table.add_column("Time", style="dim", width=20)
        table.add_column("Source", style="cyan", width=10)
        table.add_column("Type", style="magenta", width=12)
        table.add_column("Title", width=40)

        for ev in events:
            table.add_row(
                ev["timestamp"][:19],
                ev["source"],
                ev["event_type"],
                (ev["title"] or "")[:40],
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
                    f"{k}={v}" for k, v in list(meta.items())[:8]
                    if v not in (None, "", [])
                ]
                if meta_parts:
                    content += f"\n\n[dim]{'  '.join(meta_parts)}[/dim]"

            console.print(Panel(
                content,
                title=ev.get("title") or "(untitled)",
                subtitle=subtitle,
                expand=True,
            ))
    finally:
        db.close()


@cli.command(hidden=True)
@click.argument("question")
@click.pass_context
def ask(ctx: click.Context, question: str) -> None:
    """Ask a natural language question about the user."""
    from syke.distribution.ask_agent import ask as run_ask

    user_id = ctx.obj["user"]
    db = get_db(user_id)
    try:
        with console.status("[bold cyan]Syke is thinking...[/bold cyan]"):
            answer = run_ask(db, user_id, question)
        console.print(f"\n{answer}\n")
    finally:
        db.close()


@cli.command(hidden=True)
@click.option("--port", default=3847, help="Port for HTTP transport")
@click.option("--transport", type=click.Choice(["stdio", "http"]), default="stdio")
@click.pass_context
def serve(ctx: click.Context, port: int, transport: str) -> None:
    """Start the MCP server."""
    from syke.distribution.mcp_server import create_server

    user_id = ctx.obj["user"]
    server = create_server(user_id)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host="127.0.0.1", port=port)


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
        console.print(f"  [green]FOUND[/green]  claude-code    {cc_sessions} session files in ~/.claude/")

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
        console.print(f"  [green]FOUND[/green]  github         API token configured")
    else:
        # Try gh CLI
        import subprocess
        try:
            result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                sources.append(("github", "gh CLI authenticated", "gh auth"))
                console.print(f"  [green]FOUND[/green]  github         gh CLI authenticated")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            console.print(f"  [dim]SKIP[/dim]   github         No token or gh CLI found")

    # Gmail — check gog CLI first, then Python OAuth
    from syke.ingestion.gmail import _gog_authenticated, _python_oauth_available
    _gmail_account = _os.getenv("GMAIL_ACCOUNT", "")
    _gmail_found = False

    if _gmail_account and _gog_authenticated(_gmail_account):
        sources.append(("gmail", f"gog CLI ({_gmail_account})", "gog auth"))
        console.print(f"  [green]FOUND[/green]  gmail          gog CLI authenticated ({_gmail_account})")
        _gmail_found = True
    elif _python_oauth_available():
        _token_path = _Path(_os.path.expanduser(
            _os.getenv("GMAIL_TOKEN_PATH", "~/.config/syke/gmail_token.json")
        ))
        if _token_path.exists():
            sources.append(("gmail", "Python OAuth (token cached)", str(_token_path)))
            console.print(f"  [green]FOUND[/green]  gmail          Python OAuth token cached")
            _gmail_found = True
        else:
            _creds_path = _Path(_os.path.expanduser(
                _os.getenv("GMAIL_CREDENTIALS_PATH", "~/.config/syke/gmail_credentials.json")
            ))
            if _creds_path.exists():
                sources.append(("gmail", "Python OAuth (credentials ready)", str(_creds_path)))
                console.print(f"  [green]FOUND[/green]  gmail          Python OAuth credentials ready (will prompt for consent)")
                _gmail_found = True

    if not _gmail_found:
        if _python_oauth_available():
            console.print(
                f"  [yellow]READY[/yellow]  gmail          Python OAuth installed — needs credentials\n"
                f"              [dim]Download from https://console.cloud.google.com/apis/credentials[/dim]\n"
                f"              [dim]Save to: ~/.config/syke/gmail_credentials.json[/dim]"
            )
        else:
            console.print(
                f"  [dim]SKIP[/dim]   gmail          No backend available\n"
                f"              [dim]Option A: brew install gog && gog auth add --account you@gmail.com[/dim]\n"
                f"              [dim]Option B: pip install google-auth-oauthlib google-api-python-client[/dim]"
            )

    if not sources:
        console.print("[yellow]No data sources detected.[/yellow]")
    else:
        console.print(f"\n[bold]{len(sources)} source(s) available.[/bold]")
        console.print("[dim]Run: syke setup --user <name>[/dim]")


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Auto-consent to all private sources")
@click.option("--skip-mcp", is_flag=True, help="Skip MCP server setup")
@click.option("--skip-hooks", is_flag=True, help="Skip lifecycle hooks setup")
@click.option("--skip-daemon", is_flag=True, help="Skip LaunchAgent daemon install")
@click.pass_context
def setup(ctx: click.Context, yes: bool, skip_mcp: bool, skip_hooks: bool, skip_daemon: bool) -> None:
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

    source_install = _is_source_install()

    # Step 1: Check environment
    console.print("[bold]Step 1:[/bold] Checking environment...")
    from syke.config import ANTHROPIC_API_KEY, save_api_key
    has_api_key = bool(ANTHROPIC_API_KEY)
    has_claude_auth = _claude_binary_authed()
    can_perceive = has_api_key or has_claude_auth
    if has_api_key:
        if has_claude_auth:
            # Session auth is primary — don't persist the API key.
            # Persisting creates a stale-key risk: module-level load_dotenv() injects
            # ~/.syke/.env into every process, blocking session auth if the key depletes.
            console.print("  [green]OK[/green]  Claude Code session auth detected (primary for perception)")
            console.print("  [dim]  API key found but not persisted — session auth is preferred[/dim]")
        else:
            save_api_key(ANTHROPIC_API_KEY)
            console.print("  [green]OK[/green]  Anthropic API key configured")
            console.print("  [green]OK[/green]  API key persisted to ~/.syke/.env (chmod 600)")
    elif has_claude_auth:
        console.print("  [green]OK[/green]  Claude Code session auth detected (perception via ~/.claude/)")
    else:
        console.print("  [yellow]WARN[/yellow]  No auth — perception will be skipped")
        console.print("         [dim]Run 'claude login' (recommended) or set ANTHROPIC_API_KEY[/dim]")
        console.print("         [dim]Data collection, MCP, and daemon will still proceed.[/dim]")

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
                ["git", "config", "user.name"], capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                # Try GitHub username from gh CLI
                r2 = subprocess.run(
                    ["gh", "api", "user", "--jq", ".login"],
                    capture_output=True, text=True, timeout=10
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

        # Step 3: Build identity profile
        profile = None
        if can_perceive:
            console.print(f"\n[bold]Step 3:[/bold] Building identity profile from {ingested_count} events...\n")
            from syke.metrics import MetricsTracker
            from syke.perception.agentic_perceiver import AgenticPerceiver

            agentic_state: dict = {}
            on_discovery = _make_discovery_cb(agentic_state)

            tracker = MetricsTracker(user_id)
            # Unset CLAUDECODE so Agent SDK subprocess doesn't think it's nested
            _os.environ.pop('CLAUDECODE', None)
            try:
                with tracker.track("perceive_agentic", mode="full") as metrics:
                    perceiver = AgenticPerceiver(db, user_id)
                    profile = perceiver.perceive(full=True, on_discovery=on_discovery)
                    metrics.events_processed = profile.events_count
                    metrics.cost_usd = perceiver.metrics.cost_usd
                    metrics.method = "agentic"
                    metrics.num_turns = perceiver.metrics.num_turns
                    metrics.input_tokens = perceiver.metrics.input_tokens
                    metrics.output_tokens = perceiver.metrics.output_tokens
                    metrics.thinking_tokens = perceiver.metrics.thinking_tokens

                profile_path = user_profile_path(user_id)
                profile_path.write_text(profile.model_dump_json(indent=2))

                console.print(f"  [green]OK[/green]  Profile generated with {len(profile.active_threads)} active threads")
                console.print(f"  Turns: {perceiver.metrics.num_turns}")
                console.print(f"  Cost: ${perceiver.metrics.cost_usd:.4f}")
                console.print(f"  Saved to: {profile_path}")
            except Exception as e:
                console.print(f"  [yellow]SKIP[/yellow]  Profile generation failed: {e}")
                console.print(f"  [dim]Run later: syke sync --rebuild[/dim]")

            # Step 4: Output summary
            if profile is not None:
                console.print(f"\n[bold]Step 4:[/bold] Generating outputs...\n")

                from syke.distribution.formatters import format_profile

                for fmt, filename in [("claude-md", "CLAUDE.md"), ("user-md", "USER.md")]:
                    out_path = user_data_dir(user_id) / filename
                    out_path.write_text(format_profile(profile, fmt))
                    console.print(f"  [green]OK[/green]  {filename:10s} → {out_path}")
            else:
                console.print(f"\n[bold]Step 4:[/bold] [yellow]Skipped[/yellow] — no profile to format")
        else:
            console.print(f"\n[bold]Step 3:[/bold] [yellow]Skipped[/yellow] — no auth (set ANTHROPIC_API_KEY or run 'claude login')")
            console.print(f"[bold]Step 4:[/bold] [yellow]Skipped[/yellow] — no profile to format")

        # Step 5: MCP server auto-injection
        project_root = _Path(__file__).resolve().parent.parent
        if not skip_mcp:
            console.print(f"\n[bold]Step 5:[/bold] MCP server configuration\n")
            from syke.distribution.inject import inject_mcp_config, inject_mcp_config_desktop, inject_mcp_config_project
            mcp_path = inject_mcp_config(user_id, source_install=source_install)
            console.print(f"  [green]OK[/green]  Claude Code MCP (global) → {mcp_path}")
            if source_install:
                project_mcp_path = inject_mcp_config_project(user_id, project_root)
                if project_mcp_path:
                    console.print(f"  [green]OK[/green]  Claude Code MCP (project) → {project_mcp_path}")
            desktop_path = inject_mcp_config_desktop(user_id, source_install=source_install)
            if desktop_path:
                console.print(f"  [green]OK[/green]  Claude Desktop MCP → {desktop_path}")

        # Step 6: Lifecycle hooks
        if not skip_hooks:
            console.print(f"\n[bold]Step 6:[/bold] Claude Code lifecycle hooks\n")
            if source_install:
                from syke.distribution.inject import inject_hooks_config
                hooks_path = inject_hooks_config(project_root)
                console.print(f"  [green]OK[/green]  SessionStart + Stop hooks injected into {hooks_path}")
            else:
                console.print(f"  [yellow]SKIP[/yellow]  Hooks require source install (hook scripts live in the repo)")

        # Step 7: Background daemon
        if not skip_daemon:
            console.print(f"\n[bold]Step 7:[/bold] Background sync daemon\n")
            try:
                if yes:
                    ctx.invoke(daemon_start, interval=900)
                    console.print(f"  [green]OK[/green]  Daemon started. Syncs every 15 minutes.")
                else:
                    from rich.prompt import Confirm
                    if Confirm.ask("Install background sync daemon? (recommended)", default=True):
                        ctx.invoke(daemon_start, interval=900)
                        console.print(f"  [green]OK[/green]  Daemon started. Syncs every 15 minutes.")
                    else:
                        console.print(f"  [dim]Skipped daemon install. You can install later with: syke daemon-start[/dim]")
            except Exception as e:
                console.print(f"  [yellow]SKIP[/yellow]  Daemon install failed: {e}")
                console.print(f"  [dim]You can install manually with: syke daemon-start[/dim]")

        # Final summary
        if profile is not None:
            console.print("\n[bold green]Setup complete.[/bold green]")
            console.print(f"  {ingested_count} events from {len(profile.sources)} platforms")
            console.print(f"  Identity: {profile.identity_anchor[:100]}...")
            console.print(f"  Active threads: {len(profile.active_threads)}")
        elif can_perceive:
            console.print("\n[bold yellow]Setup complete — profile pending.[/bold yellow]")
            console.print(f"  {ingested_count} events collected")
            console.print("  Profile: [yellow]generation failed[/yellow]")
            console.print()
            console.print("[bold]To generate profile:[/bold]")
            console.print("  syke sync --rebuild    [dim](run in a standalone terminal, not inside Claude Code)[/dim]")
        else:
            console.print("\n[bold yellow]Setup complete — profile pending.[/bold yellow]")
            console.print(f"  {ingested_count} events collected")
            console.print("  Profile: [yellow]not generated[/yellow] (no auth)")
            console.print()
            console.print("[bold]To generate profile:[/bold]")
            console.print("  Option 1: claude login    [dim](free for Claude Code Max/Team/Enterprise)[/dim]")
            console.print("  Option 2: export ANTHROPIC_API_KEY=sk-ant-...")
            console.print("  Then run: syke sync --rebuild")
        console.print()
        console.print("[bold]Syke is now active:[/bold]")
        if not skip_mcp:
            console.print("  MCP server:  Injected into ~/.claude.json")
        if source_install and not skip_hooks:
            console.print("  Hooks:       SessionStart + Stop installed")
        if not skip_daemon:
            from syke.daemon.daemon import is_running
            running, _ = is_running()
            if running:
                console.print("  Background:  Daemon syncing every 15 minutes")
        console.print()
        console.print("[bold yellow]>>> Restart Claude Code to activate MCP. <<<[/bold yellow]")
        if profile is not None:
            console.print("From now on, every session knows who you are.")
        else:
            console.print("MCP tools ready after restart. Timeline data available now.")

    finally:
        db.close()


@cli.command()
@click.option("--rebuild", is_flag=True, help="Rebuild profile from scratch instead of incremental update")
@click.option("--skip-profile", is_flag=True, help="Only sync data, skip profile update")
@click.option("--force", is_flag=True, help="Force profile update even with few new events")
@click.pass_context
def sync(ctx: click.Context, rebuild: bool, skip_profile: bool, force: bool) -> None:
    """Sync new data and update profile.

    Pulls new events from all connected sources, then runs an incremental
    profile update if enough new data is found (minimum 5 events).

    Use --force to update even with fewer events.
    Use --rebuild for a full ground-up profile rebuild.
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

        total_new, synced = run_sync(
            db, user_id, rebuild, skip_profile, force=force
        )

        console.print(f"\n[bold]Synced {total_new} new event(s) from {len(sources)} source(s).[/bold]")
        if total_new == 0:
            console.print("[dim]Already up to date.[/dim]")

    finally:
        db.close()


@cli.command("daemon-start", hidden=True)
@click.option("--interval", type=int, default=900, help="Sync interval in seconds (default: 900 = 15 min)")
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

    console.print("[green]✓[/green] Daemon started. Sync runs every {0} minutes.".format(interval // 60))
    console.print("  Check status: syke daemon-status")
    console.print("  View logs:    syke daemon-logs")


@cli.command("daemon-stop", hidden=True)
@click.pass_context
def daemon_stop(ctx: click.Context) -> None:
    """Stop background sync daemon."""
    from syke.daemon.daemon import stop_and_unload, is_running

    running, pid = is_running()
    if not running:
        console.print("[dim]Daemon not running[/dim]")
        return

    console.print(f"[bold]Stopping daemon[/bold] (PID {pid})")
    stop_and_unload()
    console.print("[green]✓[/green] Daemon stopped.")


@cli.command("daemon-status", hidden=True)
@click.pass_context
def daemon_status(ctx: click.Context) -> None:
    """Check daemon status."""
    from syke.daemon.daemon import get_status, is_running, LOG_PATH
    from syke.daemon.metrics import MetricsTracker

    running, pid = is_running()
    user_id = ctx.obj["user"]

    console.print("[bold]Daemon status[/bold]")
    console.print(f"  Running:  {'[green]yes[/green] (PID ' + str(pid) + ')' if running else '[red]no[/red]'}")

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

    console.print(f"  Log:      {LOG_PATH}  [dim](syke daemon-logs to view)[/dim]")

    # Version info (cache-only, never hits network)
    from syke.version_check import cached_update_available
    update_avail, latest_cached = cached_update_available(__version__)
    console.print(f"  Version:  [cyan]{__version__}[/cyan]", end="")
    if update_avail and latest_cached:
        console.print(f"  [yellow]Update available: {latest_cached} — run: syke self-update[/yellow]")
    else:
        console.print()


@cli.command("daemon-logs", hidden=True)
@click.option("-n", "--lines", default=50, help="Number of lines to show (default: 50)")
@click.option("-f", "--follow", is_flag=True, help="Follow log output (like tail -f)")
@click.option("--errors", is_flag=True, help="Show only ERROR lines")
@click.pass_context
def daemon_logs(ctx: click.Context, lines: int, follow: bool, errors: bool) -> None:
    """View daemon log output."""
    import time
    from collections import deque
    from syke.daemon.daemon import LOG_PATH

    if not LOG_PATH.exists():
        console.print(f"[yellow]No daemon log found at {LOG_PATH}[/yellow]")
        console.print("[dim]Is the daemon installed? Run: syke daemon-start[/dim]")
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
            tail = [l for l in tail if " ERROR " in l]
        for line in tail:
            console.print(line)



@cli.command("self-update")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def self_update(ctx: click.Context, yes: bool) -> None:
    """Upgrade syke to the latest version from PyPI."""
    import subprocess
    from syke.version_check import check_update_available
    from syke.daemon.daemon import is_running, stop_and_unload, install_and_start

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
        console.print("\n[yellow]Installed via uvx — uvx fetches the latest version automatically.[/yellow]")
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


# Register experiment commands if available (untracked)
try:
    from experiments.cli_experiments import register_experiment_commands
    register_experiment_commands(cli)
except ImportError:
    pass
