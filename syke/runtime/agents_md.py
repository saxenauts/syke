"""Generate a minimal AGENTS.md placeholder for the Pi workspace."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENTS_MD_TEMPLATE = """# Syke Workspace

Use the workspace directly.

- `events.db` is read-only evidence.
- `syke.db` is writable learned memory.
- `MEMEX.md` is the routed memory artifact.
"""


def write_agents_md(workspace_root: Path) -> Path:
    """Write AGENTS.md into the workspace and return its path."""
    agents_md_path = workspace_root / "AGENTS.md"
    agents_md_path.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")
    logger.info("AGENTS.md written to %s", agents_md_path)
    return agents_md_path
