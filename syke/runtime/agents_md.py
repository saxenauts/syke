"""Generate AGENTS.md for the Pi workspace. Pi auto-discovers AGENTS.md in its working directory and loads it as project context."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENTS_MD_TEMPLATE = """# Syke Workspace

This is your persistent workspace. You are Syke's synthesis agent — a background process that maintains a user's personal knowledge base.

## Directory Structure

- `events.db` — immutable timeline, READ ONLY
- `agent.db` — agent owns completely
- `memex.md` — living synthesis document
- `scripts/` — persistent analysis tools
- `files/` — file storage
- `scratch/` — working memory
- `sessions/` — Pi session history, don't modify

## Database Access

When reading from the timeline, use SQLite's ATTACH pattern so `events.db` stays separate from your writable `agent.db`.

```sql
ATTACH DATABASE 'events.db' AS timeline;
SELECT * FROM timeline.events ORDER BY timestamp DESC LIMIT 10;
```

## Key Rules

1. Never write `events.db`
2. Always update `memex.md`
3. Be incremental
4. Build scripts for repeated patterns
5. Filter `source='syke'` events
"""


def write_agents_md(workspace_root: Path) -> Path:
    """Write AGENTS.md into the workspace and return its path."""
    agents_md_path = workspace_root / "AGENTS.md"
    agents_md_path.write_text(AGENTS_MD_TEMPLATE)
    logger.info("AGENTS.md written to %s", agents_md_path)
    return agents_md_path
