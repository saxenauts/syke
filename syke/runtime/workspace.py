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


def initialize_workspace() -> None:
    """Create the workspace structure.

    Called once at setup/daemon startup. Creates dirs, installs adapter
    markdowns from seeds, writes PSYCHE.md. Idempotent.

    MEMEX.md is NOT written here — synthesis owns MEMEX creation.
    syke.db is NOT created here — SykeDB constructor handles that.
    """
    import logging

    logger = logging.getLogger(__name__)

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    from syke.observe.bootstrap import ensure_adapters

    ensure_adapters(WORKSPACE_ROOT)

    from syke.runtime.psyche_md import write_psyche_md

    write_psyche_md(WORKSPACE_ROOT)

    logger.debug("Workspace initialized at %s", WORKSPACE_ROOT)
