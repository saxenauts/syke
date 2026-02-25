"""Click CLI for Syke."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from syke import __version__
from syke.config import DEFAULT_USER, _is_source_install, user_data_dir, user_db_path
from syke.db import SykeDB

console = Console()


def get_db(user_id: str) -> SykeDB:
    """Get an initialized DB for a user."""
    return SykeDB(user_db_path(user_id))


@click.group(invoke_without_command=True)
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
            console.print(
                f"\n[bold]Memex[/bold]: synthesized at {created} ({mem_count} memories)"
            )
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
@click.option(
    "--query", default=None, help="Custom Gmail search query (overrides auto-filter)"
)
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
@click.option(
    "--yes", "-y", is_flag=True, help="Skip consent prompts for private sources"
)
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
@click.option(
    "--target", "-t", required=True, type=click.Path(), help="Target directory"
)
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

        from datetime import datetime, timezone

        def _fmt_time(ts: str) -> str:
            """Format ISO timestamp as readable local time."""
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                dt_local = dt.astimezone()
                now = datetime.now(timezone.utc).astimezone()
                if dt_local.date() == now.date():
                    return f"[bold]today[/bold] {dt_local.strftime('%H:%M:%S')}"
                elif (now.date() - dt_local.date()).days == 1:
                    return f"[dim]yesterday[/dim] {dt_local.strftime('%H:%M:%S')}"
                else:
                    return dt_local.strftime("%b %d  %H:%M:%S")
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
                    f"{k}={v}"
                    for k, v in list(meta.items())[:8]
                    if v not in (None, "", [])
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
        console.print(
            f"  [green]FOUND[/green]  claude-code    {cc_sessions} session files in ~/.claude/"
        )

    # ChatGPT exports
    downloads = _Path(_os.path.expanduser("~/Downloads"))
    chatgpt_zips = list(downloads.glob("*chatgpt*.zip")) + list(
        downloads.glob("*ChatGPT*.zip")
    )
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
            console.print(
                f"  [green]FOUND[/green]  chatgpt        {size_mb:.0f} MB — {zf.name}"
            )

    # GitHub (check for token or public access)
    gh_token = _os.getenv("GITHUB_TOKEN", "")
    if gh_token:
        sources.append(("github", "API token configured", "GITHUB_TOKEN env"))
        console.print(f"  [green]FOUND[/green]  github         API token configured")
    else:
        # Try gh CLI
        import subprocess

        try:
            result = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                sources.append(("github", "gh CLI authenticated", "gh auth"))
                console.print(
                    f"  [green]FOUND[/green]  github         gh CLI authenticated"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            console.print(
                f"  [dim]SKIP[/dim]   github         No token or gh CLI found"
            )

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
            _os.path.expanduser(
                _os.getenv("GMAIL_TOKEN_PATH", "~/.config/syke/gmail_token.json")
            )
        )
        if _token_path.exists():
            sources.append(("gmail", "Python OAuth (token cached)", str(_token_path)))
            console.print(
                f"  [green]FOUND[/green]  gmail          Python OAuth token cached"
            )
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
                sources.append(
                    ("gmail", "Python OAuth (credentials ready)", str(_creds_path))
                )
                console.print(
                    f"  [green]FOUND[/green]  gmail          Python OAuth credentials ready (will prompt for consent)"
                )
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
def setup(
    ctx: click.Context, yes: bool, skip_mcp: bool, skip_hooks: bool, skip_daemon: bool
) -> None:
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
    has_claude_auth = _claude_is_authenticated()
    if has_claude_auth:
        console.print(
            "  [green]OK[/green]  Claude Code session auth detected (synthesis via ~/.claude/)"
        )
    else:
        console.print("  [red]FAIL[/red]  No auth \u2014 synthesis requires Claude Code login")
        console.print("         [dim]Run 'claude login' to authenticate[/dim]")
        console.print("         [dim]Syke requires auth for synthesis. No data-only mode.[/dim]")
        console.print("         [dim]Then re-run: syke setup --yes[/dim]")
        return

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
            console.print(
                f"  [green]OK[/green]  Claude Code: {result.events_count} sessions"
            )
            ingested_count += result.events_count

        # ChatGPT export
        downloads = _Path(_os.path.expanduser("~/Downloads"))
        chatgpt_zip = None
        for zf in sorted(
            downloads.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
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
            console.print(
                f"  [cyan]Ingesting ChatGPT export...[/cyan] ({chatgpt_zip.name})"
            )
            from syke.ingestion.chatgpt import ChatGPTAdapter
            from syke.metrics import MetricsTracker

            tracker = MetricsTracker(user_id)
            with tracker.track("ingest_chatgpt") as metrics:
                adapter = ChatGPTAdapter(db, user_id)
                result = adapter.ingest(file_path=str(chatgpt_zip))
                metrics.events_processed = result.events_count
            console.print(
                f"  [green]OK[/green]  ChatGPT: {result.events_count} conversations"
            )
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
                console.print(
                    f"  [green]OK[/green]  GitHub: {result.events_count} events"
                )
                ingested_count += result.events_count
            except Exception as e:
                console.print(f"  [yellow]WARN[/yellow]  GitHub: {e}")

        # Check total events in DB (including previously ingested)
        total_in_db = db.count_events(user_id)
        if ingested_count == 0 and total_in_db == 0:
            console.print("[yellow]No data sources found to ingest.[/yellow]")
            return
        if ingested_count == 0 and total_in_db > 0:
            console.print(
                f"  [dim]No new events ({total_in_db} already collected)[/dim]"
            )
            ingested_count = total_in_db

        # Step 3: Run synthesis (replaces old perception step)
        synthesis_ok = False
        if ingested_count >= 5:
            console.print(
                f"\n[bold]Step 3:[/bold] Synthesizing identity from {ingested_count} events...\n"
            )
            from syke.memory.synthesis import synthesize

            try:
                result = synthesize(db, user_id, force=True)
                if result.get("status") == "error":
                    console.print(
                        f"  [yellow]SKIP[/yellow]  Synthesis failed: {result.get('error', 'unknown')}"
                    )
                    console.print(f"  [dim]Run later: syke sync --rebuild[/dim]")
                else:
                    synthesis_ok = True
                    cost = result.get("cost_usd", 0.0)
                    console.print(f"  [green]OK[/green]  Synthesis complete")
                    if cost:
                        console.print(f"  Cost: ${cost:.4f}")
            except Exception as e:
                console.print(f"  [yellow]SKIP[/yellow]  Synthesis failed: {e}")
                console.print(f"  [dim]Run later: syke sync --rebuild[/dim]")

            # Step 4: Write memex outputs
            if synthesis_ok:
                console.print(f"\n[bold]Step 4:[/bold] Generating outputs...\n")
                from syke.memory.memex import get_memex_for_injection

                memex_content = get_memex_for_injection(db, user_id)
                for filename in ["CLAUDE.md", "USER.md"]:
                    out_path = user_data_dir(user_id) / filename
                    out_path.write_text(memex_content)
                    console.print(f"  [green]OK[/green]  {filename:10s} → {out_path}")
            else:
                console.print(
                    f"\n[bold]Step 4:[/bold] [yellow]Skipped[/yellow] — synthesis did not complete"
                )
        else:
            console.print(
                f"\n[bold]Step 3:[/bold] [yellow]Skipped[/yellow] \u2014 not enough data yet for synthesis ({ingested_count} events, need 5+)"
            )
            console.print("  [dim]Daemon will synthesize once enough data is collected.[/dim]")
            console.print(
                f"[bold]Step 4:[/bold] [yellow]Skipped[/yellow] \u2014 no memex to format"
            )

        # Step 5: MCP server auto-injection
        project_root = _Path(__file__).resolve().parent.parent
        if not skip_mcp:
            console.print(f"\n[bold]Step 5:[/bold] MCP server configuration\n")
            from syke.distribution.inject import (
                inject_mcp_config,
                inject_mcp_config_desktop,
                inject_mcp_config_project,
            )

            mcp_path = inject_mcp_config(user_id, source_install=source_install)
            console.print(f"  [green]OK[/green]  Claude Code MCP (global) → {mcp_path}")
            if source_install:
                project_mcp_path = inject_mcp_config_project(user_id, project_root)
                if project_mcp_path:
                    console.print(
                        f"  [green]OK[/green]  Claude Code MCP (project) → {project_mcp_path}"
                    )
            desktop_path = inject_mcp_config_desktop(
                user_id, source_install=source_install
            )
            if desktop_path:
                console.print(
                    f"  [green]OK[/green]  Claude Desktop MCP → {desktop_path}"
                )

        # Step 6: Lifecycle hooks
        if not skip_hooks:
            console.print(f"\n[bold]Step 6:[/bold] Claude Code lifecycle hooks\n")
            if source_install:
                from syke.distribution.inject import inject_hooks_config

                hooks_path = inject_hooks_config(project_root)
                console.print(
                    f"  [green]OK[/green]  SessionStart + Stop hooks injected into {hooks_path}"
                )
            else:
                console.print(
                    f"  [yellow]SKIP[/yellow]  Hooks require source install (hook scripts live in the repo)"
                )

        # Step 7: Background daemon
        if not skip_daemon:
            console.print(f"\n[bold]Step 7:[/bold] Background sync daemon\n")
            try:
                if yes:
                    ctx.invoke(start, interval=900)
                    console.print(
                        f"  [green]OK[/green]  Daemon started. Syncs every 15 minutes."
                    )
                else:
                    from rich.prompt import Confirm

                    if Confirm.ask(
                        "Install background sync daemon? (recommended)", default=True
                    ):
                        ctx.invoke(start, interval=900)
                        console.print(
                            f"  [green]OK[/green]  Daemon started. Syncs every 15 minutes."
                        )
                    else:
                        console.print(
                            f"  [dim]Skipped daemon install. You can install later with: syke daemon start[/dim]"
                        )
            except Exception as e:
                console.print(f"  [yellow]SKIP[/yellow]  Daemon install failed: {e}")
                console.print(
                    f"  [dim]You can install manually with: syke daemon start[/dim]"
                )

        # Final summary
        if synthesis_ok:
            memex = db.get_memex(user_id)
            mem_count = db.count_memories(user_id)
            console.print("\n[bold green]Setup complete.[/bold green]")
            console.print(f"  {ingested_count} events collected")
            console.print(f"  Memex: [green]synthesized[/green] ({mem_count} memories)")
            if memex:
                content_preview = memex.get("content", "")[:100]
                if content_preview:
                    console.print(f"  Preview: {content_preview}...")
        else:
            console.print(
                "\n[bold yellow]Setup complete \u2014 synthesis pending.[/bold yellow]"
            )
            console.print(f"  {ingested_count} events collected")
            console.print("  Memex: [yellow]not yet synthesized[/yellow]")
            console.print()
            console.print("[bold]To synthesize:[/bold]")
            console.print(
                "  syke sync --rebuild    [dim](run in a standalone terminal, not inside Claude Code)[/dim]"
            )
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
        console.print(
            "[bold yellow]>>> Restart Claude Code to activate MCP. <<<[/bold yellow]"
        )
        if synthesis_ok:
            console.print("From now on, every session knows who you are.")
        else:
            console.print("MCP tools ready after restart. Timeline data available now.")

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

    console.print(
        "[green]✓[/green] Daemon started. Sync runs every {0} minutes.".format(
            interval // 60
        )
    )
    console.print("  Check status: syke daemon status")
    console.print("  View logs:    syke daemon logs")


@daemon.command()
@click.pass_context
def stop(ctx: click.Context) -> None:
    """Stop background sync daemon."""
    from syke.daemon.daemon import stop_and_unload, is_running

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
    from syke.daemon.daemon import get_status, is_running, LOG_PATH
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
        console.print(
            "  [yellow]Could not reach PyPI — check your connection.[/yellow]"
        )
        return
    if not update_available:
        console.print("[green]Already up to date.[/green]")
        return

    method = _detect_install_method()

    if method == "uvx":
        console.print(
            "\n[yellow]Installed via uvx — uvx fetches the latest version automatically.[/yellow]"
        )
        console.print(
            "  No action needed: uvx syke ... always uses the latest PyPI release."
        )
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
    from syke.daemon.daemon import is_running

    console.print(f"[bold]Syke[/bold] v{__version__}  ·  user: {user_id}\n")

    # Auth
    authed = _claude_is_authenticated()
    auth_label = "[green]OK[/green]" if authed else "[red]MISSING[/red]"
    console.print(f"  Auth:    {auth_label}")

    # Daemon
    running, pid = is_running()
    if running:
        daemon_label = f"[green]running[/green] (PID {pid})"
    else:
        daemon_label = "[dim]stopped[/dim]"
    console.print(f"  Daemon:  {daemon_label}")

    # DB stats
    db_path = user_db_path(user_id)
    if db_path.exists():
        db = get_db(user_id)
        try:
            count = db.count_events(user_id)
            status = db.get_status(user_id)
            last_event = status.get("latest_event_at", "never")
            console.print(f"  Events:  {count}")
            console.print(f"  Last:    {last_event or 'never'}")
        finally:
            db.close()
    else:
        console.print("  DB:      [dim]not initialized[/dim]")

    # Memex
    memex_dir = user_data_dir(user_id)
    memex_file = memex_dir / "memex.md"
    if memex_file.exists():
        import time

        age_s = time.time() - memex_file.stat().st_mtime
        if age_s < 3600:
            age_str = f"{int(age_s / 60)}m ago"
        elif age_s < 86400:
            age_str = f"{int(age_s / 3600)}h ago"
        else:
            age_str = f"{int(age_s / 86400)}d ago"
        console.print(f"  Memex:   [green]exists[/green] (updated {age_str})")
    else:
        console.print("  Memex:   [dim]not yet generated[/dim]")

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
@click.option("--format", "fmt", type=click.Choice(["json", "markdown"]), default="markdown", help="Output format")
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
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Verify Syke installation health."""
    import shutil

    from syke.daemon.daemon import is_running

    user_id = ctx.obj["user"]
    console.print(f"[bold]Syke Doctor[/bold]  ·  user: {user_id}\n")

    # Claude binary
    has_binary = bool(shutil.which("claude"))
    _print_check("Claude binary", has_binary, "in PATH" if has_binary else "not found — install Claude Code")

    # Auth
    has_auth = _claude_is_authenticated()
    _print_check("Claude auth", has_auth, "~/.claude/ has tokens" if has_auth else "run 'claude login'")

    # Database
    db_path = user_db_path(user_id)
    has_db = db_path.exists()
    _print_check("Database", has_db, str(db_path) if has_db else "not found — run 'syke setup'")

    # Daemon
    running, pid = is_running()
    _print_check("Daemon", running, f"PID {pid}" if running else "not running — run 'syke daemon start'")

    # Event count
    if has_db:
        db = get_db(user_id)
        try:
            count = db.count_events(user_id)
            console.print(f"  Events: {count}")
        finally:
            db.close()


# ---------------------------------------------------------------------------
# syke mcp serve
# ---------------------------------------------------------------------------


@cli.group("mcp")
def mcp_group() -> None:
    """MCP server management."""
    pass


@mcp_group.command("serve")
@click.option("--port", default=3847, help="Port for HTTP transport")
@click.option("--transport", type=click.Choice(["stdio", "http"]), default="stdio")
@click.pass_context
def mcp_serve(ctx: click.Context, port: int, transport: str) -> None:
    """Start the MCP server (stdio by default)."""
    from syke.distribution.mcp_server import create_server

    user_id = ctx.obj["user"]
    server = create_server(user_id)
    if transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="streamable-http", host="127.0.0.1", port=port)


# Register experiment commands if available (untracked)
try:
    from experiments.cli_experiments import register_experiment_commands

    register_experiment_commands(cli)
except ImportError:
    pass
