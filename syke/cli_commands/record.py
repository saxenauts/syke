"""Record command for the Syke CLI."""

from __future__ import annotations

import json
import sys
from typing import cast

import click
from rich.console import Console

from syke.cli_support.context import get_db

console = Console()


@click.command(short_help="Add a note or observation.")
@click.argument("text", required=False)
@click.option("--tag", "-t", multiple=True, help="Tag(s) for categorization")
@click.option("--source", "-s", default="record", help="Source label (default: record)")
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
    """Record an observation, note, or research dump into Syke."""
    from syke.observe.importers import IngestGateway

    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        gw = IngestGateway(db, user_id)

        if use_jsonl:
            if not sys.stdin.isatty():
                lines = sys.stdin.read().strip().splitlines()
            elif text:
                lines = text.strip().splitlines()
            else:
                raise click.UsageError("--jsonl requires piped input or text argument")

            events = []
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise click.UsageError(f"Line {i + 1}: invalid JSON — {e}") from None

            if not events:
                console.print("[dim]No events to record.[/dim]")
                return

            result = gw.push_batch(events)
            console.print(
                f"Recorded [green]{result['inserted']}[/green] events"
                f" ({result['duplicates']} duplicates, {result['filtered']} filtered)"
            )
            return

        if use_json:
            raw = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
            if not raw:
                raise click.UsageError("--json requires a JSON string as argument or stdin")

            try:
                ev = json.loads(raw)
            except json.JSONDecodeError as e:
                raise click.UsageError(f"Invalid JSON: {e}") from None

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

        content = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not content:
            raise click.UsageError("Nothing to record. Pass text as argument or pipe stdin.")

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
