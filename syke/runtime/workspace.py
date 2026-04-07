"""
Workspace path constants for the Pi agent runtime.

~/.syke/ is the agent's home. It contains the runtime workspace
(MEMEX, PSYCHE, adapters, sessions, syke.db symlink) alongside
the data/ directory which holds canonical per-user databases.
"""

from __future__ import annotations

import os
from pathlib import Path

_WORKSPACE_ROOT_OVERRIDE = os.environ.get("SYKE_WORKSPACE_ROOT", "~/.syke")
WORKSPACE_ROOT = Path(os.path.expanduser(_WORKSPACE_ROOT_OVERRIDE))

# Session storage for Pi JSONL audit trail
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"

# Canonical learned-memory database (symlinked from ~/.syke/data/{user}/syke.db)
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
