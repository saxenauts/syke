"""Context file distribution — writes memex to client context files.

After MCP removal, the memex must be pre-loaded into client context files
so agents have identity context at session start. This module handles
writing the memex to files that AI clients read automatically.

Distribution flow:
  1. distribute_memex() writes the current memex to ~/.syke/data/{user}/CLAUDE.md
  2. ensure_claude_include() adds @~/.syke/data/{user}/CLAUDE.md to ~/.claude/CLAUDE.md
  3. Every new Claude Code session reads ~/.claude/CLAUDE.md, follows the include,
     and gets the latest memex automatically.

Called from: syke setup (initial), sync.py (after every synthesis cycle).
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from syke.db import SykeDB

log = logging.getLogger(__name__)

# Claude Code global context file
CLAUDE_GLOBAL_MD = Path.home() / ".claude" / "CLAUDE.md"


def distribute_memex(db: SykeDB, user_id: str) -> Path | None:
    """Write current memex to the user's Syke data dir.

    This file is the source that ~/.claude/CLAUDE.md includes via @-reference.
    Returns the path written, or None if no memex content available.
    """
    from syke.config import user_data_dir
    from syke.memory.memex import get_memex_for_injection

    content = get_memex_for_injection(db, user_id)
    if not content or content.startswith("[No "):
        return None

    out_path = user_data_dir(user_id) / "CLAUDE.md"
    out_path.write_text(content)
    log.debug("Wrote memex to %s (%d bytes)", out_path, len(content))
    return out_path


def ensure_claude_include(user_id: str) -> bool:
    """Add @-include for Syke memex to ~/.claude/CLAUDE.md if not present.

    Claude Code reads ~/.claude/CLAUDE.md at session start and follows
    @path includes. This adds one line pointing to the Syke memex file.

    Returns True if include was added or already present, False on error.
    """
    include_line = f"@~/.syke/data/{user_id}/CLAUDE.md"

    try:
        CLAUDE_GLOBAL_MD.parent.mkdir(parents=True, exist_ok=True)

        if CLAUDE_GLOBAL_MD.exists():
            existing = CLAUDE_GLOBAL_MD.read_text()
            # Check if any form of this include already exists
            if f".syke/data/{user_id}/CLAUDE.md" in existing:
                log.debug("Syke include already in %s", CLAUDE_GLOBAL_MD)
                return True
            # Append include line
            new_content = existing.rstrip() + f"\n\n{include_line}\n"
            CLAUDE_GLOBAL_MD.write_text(new_content)
        else:
            CLAUDE_GLOBAL_MD.write_text(f"{include_line}\n")

        log.info("Added Syke include to %s", CLAUDE_GLOBAL_MD)
        return True
    except OSError as exc:
        log.warning("Failed to update %s: %s", CLAUDE_GLOBAL_MD, exc)
        return False
