"""Generate the minimal AGENTS.md bootstrap for the Pi workspace."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENTS_MD_TEMPLATE = """# Syke Pi Workspace

Syke already prepared this workspace contract for you.

- `events.db` — immutable evidence snapshot, READ ONLY
- `memory.db` — mutable learned memory space
- `MEMEX.md` — shared routed memory artifact
- `scripts/`, `files/`, `scratch/` — agent-owned workspace
- `sessions/` — Pi session history, do not edit

```sql
ATTACH DATABASE 'events.db' AS timeline;
SELECT * FROM timeline.events ORDER BY timestamp DESC LIMIT 10;
```

Never write `events.db`.
"""


def write_agents_md(workspace_root: Path) -> Path:
    """Write AGENTS.md into the workspace and return its path."""
    agents_md_path = workspace_root / "AGENTS.md"
    agents_md_path.write_text(AGENTS_MD_TEMPLATE)
    logger.info("AGENTS.md written to %s", agents_md_path)
    return agents_md_path
