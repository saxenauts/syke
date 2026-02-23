"""Sync business logic — reusable by CLI and daemon."""

from __future__ import annotations

import json
import os
import subprocess

from rich.console import Console

from syke.config import user_data_dir
from syke.db import SykeDB


def detect_github_username(db: SykeDB, user_id: str) -> str | None:
    """Detect GitHub username from DB metadata (prior sync) or gh CLI."""
    row = db.conn.execute(
        "SELECT metadata FROM events WHERE user_id = ? AND source = 'github' AND event_type = 'profile' LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        try:
            meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            login = meta.get("login")
            if login:
                return login
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


def sync_source(
    db: SykeDB, user_id: str, source: str, tracker, log: Console,
) -> int:
    """Sync a single source. Returns count of new events."""
    if source == "chatgpt":
        log.print(f"  [dim]SKIP[/dim] chatgpt (one-time import)")
        return 0

    if source == "github":
        gh_username = detect_github_username(db, user_id)
        if not gh_username:
            log.print(f"  [yellow]SKIP[/yellow] github — could not detect username")
            return 0
        from syke.ingestion.github_ import GitHubAdapter
        adapter = GitHubAdapter(db, user_id)
        kwargs = {"username": gh_username}
        label = f"github (@{gh_username})"
    elif source == "claude-code":
        from syke.ingestion.claude_code import ClaudeCodeAdapter
        adapter = ClaudeCodeAdapter(db, user_id)
        kwargs = {}
        label = "claude-code"
    elif source == "gmail":
        from syke.ingestion.gmail import GmailAdapter
        adapter = GmailAdapter(db, user_id)
        kwargs = {}
        label = "gmail"
    else:
        log.print(f"  [dim]SKIP[/dim] {source} (unknown)")
        return 0

    try:
        with tracker.track(f"sync_{source}") as metrics:
            result = adapter.ingest(**kwargs)
            metrics.events_processed = result.events_count

        if result.events_count > 0:
            log.print(f"  [green]+{result.events_count}[/green] {label}")
        else:
            log.print(f"  [dim] 0[/dim] {label}")
        return result.events_count
    except Exception as e:
        log.print(f"  [yellow]WARN[/yellow] {label}: {e}")
        return 0


SYNC_EVENT_THRESHOLD = 5  # Minimum new events before triggering profile update


def _run_memory_synthesis(db: SykeDB, user_id: str, total_new: int, log: Console) -> None:
    try:
        from syke.memory.synthesis import synthesize
        result = synthesize(db, user_id)
        status = result.get("status", "unknown")
        if status == "ok":
            cost = result.get("cost_usd", 0)
            log.print(f"  [green]Memory synthesized.[/green] Cost: ${cost:.4f}")
        elif status == "skipped":
            log.print(f"  [dim]Memory synthesis skipped (below threshold)[/dim]")
        elif status == "error":
            log.print(f"  [yellow]WARN[/yellow] Memory synthesis: {result.get('error', 'unknown')}")
    except Exception as e:
        log.print(f"  [yellow]WARN[/yellow] Memory synthesis failed: {e}")


def run_sync(
    db: SykeDB,
    user_id: str,
    rebuild: bool = False,
    skip_profile: bool = False,
    force: bool = False,
    out: Console | None = None,
) -> tuple[int, list[str]]:
    """Core sync logic reusable by CLI and daemon.

    Returns (total_new_events, list_of_synced_sources).

    Profile update is skipped if fewer than SYNC_EVENT_THRESHOLD new events
    were found, unless force=True or rebuild=True.
    """
    from syke.metrics import MetricsTracker

    tracker = MetricsTracker(user_id)
    log = out or Console()

    sources = db.get_sources(user_id)
    if not sources:
        return 0, []

    total_new = 0
    synced: list[str] = []

    for source in sources:
        count = sync_source(db, user_id, source, tracker, log)
        total_new += count
        if count >= 0 and source != "chatgpt":
            synced.append(source)

    # Also count events pushed via MCP (federated push path) since last synthesis.
    last_synthesis_ts = db.get_last_synthesis_timestamp(user_id)
    if last_synthesis_ts:
        pushed_since = db.count_events_since(user_id, last_synthesis_ts)
        extra_pushed = max(0, pushed_since - total_new)
        if extra_pushed > 0:
            log.print(f"  [green]+{extra_pushed}[/green] pushed events (via MCP)")
            total_new += extra_pushed

    _run_memory_synthesis(db, user_id, total_new, log)

    return total_new, synced
