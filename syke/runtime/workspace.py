"""
Workspace path constants for the Pi agent runtime.

~/.syke/ is the agent's home and the canonical data store.
Everything lives here: syke.db, MEMEX, PSYCHE, adapters, sessions.
"""

from __future__ import annotations

import os
from pathlib import Path

_WORKSPACE_ROOT_OVERRIDE = os.environ.get("SYKE_WORKSPACE_ROOT", "~/.syke")
WORKSPACE_ROOT = Path(os.path.expanduser(_WORKSPACE_ROOT_OVERRIDE))

# Session storage for Pi JSONL audit trail
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"

# Canonical learned-memory database
SYKE_DB = WORKSPACE_ROOT / "syke.db"

# Memex projected from canonical memory
MEMEX_PATH = WORKSPACE_ROOT / "MEMEX.md"


def set_workspace_root(root: Path | str) -> None:
    """Override workspace paths (used by tests)."""
    global WORKSPACE_ROOT, SESSIONS_DIR, SYKE_DB, MEMEX_PATH
    WORKSPACE_ROOT = Path(os.path.expanduser(str(root)))
    SESSIONS_DIR = WORKSPACE_ROOT / "sessions"
    SYKE_DB = WORKSPACE_ROOT / "syke.db"
    MEMEX_PATH = WORKSPACE_ROOT / "MEMEX.md"


def prepare_workspace(db, user_id: str) -> None:
    """Ensure the workspace is ready before Pi runs.

    Creates dirs, projects MEMEX, installs adapters, writes PSYCHE.
    Idempotent — safe to call multiple times.
    """
    import logging

    logger = logging.getLogger(__name__)

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Install adapter markdowns from seeds
    from syke.observe.bootstrap import ensure_adapters

    ensure_adapters(WORKSPACE_ROOT)

    # Write agent identity
    from syke.runtime.psyche_md import write_psyche_md

    write_psyche_md(WORKSPACE_ROOT)

    logger.debug("Workspace ready at %s", WORKSPACE_ROOT)
