"""Record command for the Syke CLI."""

from __future__ import annotations

import json
import sys

import click
from rich.console import Console
from uuid_extensions import uuid7

from syke.cli_support.context import get_db
from syke.models import Memory

console = Console()


@click.command(short_help="Add a note or observation.")
@click.argument("text", required=False)
@click.option(
    "--json",
    "use_json",
    is_flag=True,
    help="Parse TEXT or stdin as a single JSON memory",
)
@click.option(
    "--jsonl",
    "use_jsonl",
    is_flag=True,
    help="Parse stdin as newline-delimited JSON memories (batch)",
)
@click.pass_context
def record(
    ctx: click.Context,
    text: str | None,
    use_json: bool,
    use_jsonl: bool,
) -> None:
    """Record an observation, note, or research dump into Syke.

    Records become memories — available to synthesis and ask immediately.
    """
    user_id = ctx.obj["user"]
    db = get_db(user_id)

    try:
        if use_jsonl:
            if not sys.stdin.isatty():
                lines = sys.stdin.read().strip().splitlines()
            elif text:
                lines = text.strip().splitlines()
            else:
                raise click.UsageError("--jsonl requires piped input or text argument")

            inserted = 0
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise click.UsageError(f"Line {i + 1}: invalid JSON — {e}") from None

                content = obj.get("text") or obj.get("content") or ""
                if not content:
                    continue
                mem = Memory(id=str(uuid7()), user_id=user_id, content=content)
                db.insert_memory(mem)
                inserted += 1

            console.print(f"Recorded [green]{inserted}[/green] memories")
            return

        if use_json:
            raw = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
            if not raw:
                raise click.UsageError("--json requires a JSON string as argument or stdin")

            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                raise click.UsageError(f"Invalid JSON: {e}") from None

            content = obj.get("text") or obj.get("content") or ""
            if not content:
                raise click.UsageError("JSON object must have 'text' or 'content' field")
            mem = Memory(id=str(uuid7()), user_id=user_id, content=content)
            mid = db.insert_memory(mem)
            console.print(f"Recorded. [dim]({mid[:8]})[/dim]")
            return

        content = text or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
        if not content:
            raise click.UsageError("Nothing to record. Pass text as argument or pipe stdin.")

        mem = Memory(id=str(uuid7()), user_id=user_id, content=content)
        mid = db.insert_memory(mem)
        console.print(f"Recorded. [dim]({mid[:8]})[/dim]")
    finally:
        db.close()
