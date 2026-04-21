"""Top-level dashboard rendering for bare `syke` invocations."""

from __future__ import annotations

from syke import __version__
from syke.cli_support.context import get_db
from syke.cli_support.daemon_state import daemon_payload
from syke.cli_support.providers import provider_payload
from syke.cli_support.render import console
from syke.config import user_syke_db_path


def show_dashboard(user_id: str) -> None:
    """Show a quick status dashboard when `syke` runs without a subcommand."""
    console.print(f"[bold]Syke[/bold] v{__version__}  ·  user: {user_id}\n")

    provider = provider_payload()
    if provider.get("configured") and provider.get("id"):
        auth_label = f"[green]{provider['id']}[/green]"
    else:
        auth_label = "[yellow]not configured[/yellow]"
    console.print(f"  Provider: {auth_label}")

    daemon = daemon_payload()
    if daemon.get("stale"):
        daemon_label = "[yellow]stale[/yellow] (launchd registration broken)"
    elif daemon.get("running") and daemon.get("pid") is not None:
        daemon_label = f"[green]running[/green] (PID {daemon['pid']})"
    elif daemon.get("registered"):
        daemon_label = f"[yellow]registered[/yellow] ({daemon.get('detail')})"
    else:
        daemon_label = "[dim]stopped[/dim]"
    console.print(f"  Daemon:  {daemon_label}")

    syke_db_path = user_syke_db_path(user_id)
    if not syke_db_path.exists():
        console.print("  DB:      [dim]not initialized[/dim]")
        console.print("\n  Run [bold]syke --help[/bold] for commands.")
        return

    db = get_db(user_id)
    try:
        memex = db.get_memex(user_id)
        if memex:
            mem_count = db.count_memories(user_id)
            cycle_count = db.conn.execute(
                "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
                (user_id,),
            ).fetchone()[0]
            console.print(f"  Memory:  {mem_count} memories, {cycle_count} cycles")
            console.print("  Memex:   [green]synthesized[/green]")
        else:
            console.print("  Memex:   [yellow]not yet synthesized[/yellow] — run: syke sync")
    finally:
        db.close()

    console.print("\n  Run [bold]syke --help[/bold] for commands.")
