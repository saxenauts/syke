"""Syke's operating notes file, shown in every fresh operation.

``workspace/OPERATING.md`` is Syke's own text, edited with its ordinary file
tools. The host only reads it into the prompt and, once, seeds it from the
older ``syke-learned`` memory row. That row is now an ordinary memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from syke.memory.memex_budget import MEMEX_TOKEN_ENCODING, _memex_encoding

LEARNED_MEMORY_ID = "syke-learned"
OPERATING_NOTES_FILENAME = "OPERATING.md"
OPERATING_NOTES_TOKEN_LIMIT = 2_000
OPERATING_NOTES_SEEDED_MARKER = "operating-notes-seeded"


def measure_learned_projection(content: str) -> dict[str, int | str | bool]:
    """Measure text against the operating-notes display bound.

    Kept for compatibility: Syke's own stored procedures import this name.
    It used to measure the ``syke-learned`` row against a hard 1,000-token
    limit. It now measures any text, such as the contents of OPERATING.md,
    against the 2,000-token bound past which the prompt shows only the start
    of the file. Going over is not rejected anywhere.
    """
    tokens = len(_memex_encoding().encode_ordinary(content.strip()))
    return {
        "tokens": tokens,
        "limit": OPERATING_NOTES_TOKEN_LIMIT,
        "encoding": MEMEX_TOKEN_ENCODING,
        "fill_pct": min(100, round(tokens / OPERATING_NOTES_TOKEN_LIMIT * 100)),
        "over_budget": tokens > OPERATING_NOTES_TOKEN_LIMIT,
    }


def operating_notes_path(workspace_root: Path) -> Path:
    return workspace_root.expanduser().resolve() / OPERATING_NOTES_FILENAME


def render_operating_notes_block(workspace_root: Path) -> str:
    """Render the operating notes prompt section without writing anything."""
    path = operating_notes_path(workspace_root)
    try:
        body = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        body = ""
    except (OSError, UnicodeDecodeError):
        return f"# Operating notes\n\n`{path}` exists but could not be read for this run."
    if not body:
        return (
            "# Operating notes\n\n"
            f"There are no operating notes yet. They live at `{path}` and are yours to write."
        )

    encoding = _memex_encoding()
    tokens = encoding.encode_ordinary(body)
    if len(tokens) <= OPERATING_NOTES_TOKEN_LIMIT:
        return f"# Operating notes\n\nFrom `{path}`:\n\n{body}"
    shown = encoding.decode(tokens[:OPERATING_NOTES_TOKEN_LIMIT])
    last_break = shown.rfind("\n")
    if last_break > len(shown) // 2:
        shown = shown[:last_break]
    shown = shown.rstrip()
    remaining = len(tokens) - len(encoding.encode_ordinary(shown))
    return (
        f"# Operating notes\n\nFrom `{path}`:\n\n{shown}\n\n"
        f"(About {remaining:,} more tokens of this file are not shown here. "
        f"The full file is `{path}`.)"
    )


def get_learned_memory(db: Any, user_id: str) -> dict[str, Any] | None:
    """Return the older ``syke-learned`` memory row, if it still exists."""
    row = db.conn.execute(
        """SELECT id, user_id, content, created_at, updated_at
           FROM memories
           WHERE id = ? AND user_id = ?""",
        (LEARNED_MEMORY_ID, user_id),
    ).fetchone()
    return dict(row) if row is not None else None


def seed_operating_notes(
    db: Any,
    user_id: str,
    workspace_root: Path,
    control_dir: Path,
) -> Path | None:
    """Copy the ``syke-learned`` row into OPERATING.md once, if the file is absent.

    A host-owned marker in ``control_dir`` makes this happen at most once, so a
    file Syke later deletes is not brought back. The row itself is left alone.
    """
    marker = control_dir / OPERATING_NOTES_SEEDED_MARKER
    if marker.exists():
        return None
    path = operating_notes_path(workspace_root)
    seeded: Path | None = None
    if not path.exists():
        row = get_learned_memory(db, user_id)
        content = str(row.get("content") or "") if row else ""
        lines = content.lstrip("\n").split("\n")
        if lines and lines[0].strip() == "# Learned":
            content = "\n".join(lines[1:]).lstrip("\n")
        if content.strip():
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content if content.endswith("\n") else content + "\n")
            seeded = path
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{seeded or 'nothing to seed'}\n", encoding="utf-8")
    return seeded
