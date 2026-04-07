"""
Workspace path constants for the Pi agent runtime.

The agent's home is ~/.syke/. The workspace subdirectory holds Pi sessions
and runtime artifacts. In v2, the agent reads harness data directly through
adapter markdowns — no events.db snapshot, no copy pipeline.

TODO: The workspace/ subdirectory is legacy indirection. Pi could run from
~/.syke/ directly — the OS sandbox is the real boundary, not the directory
structure. Collapsing workspace/ into ~/.syke/ requires updating pi_client.py
(PiRuntime cwd parameter), daemon.py, pi_ask.py, pi_synthesis.py.
"""

from __future__ import annotations

import os
from pathlib import Path

_WORKSPACE_ROOT_OVERRIDE = os.environ.get("SYKE_WORKSPACE_ROOT", "~/.syke/workspace")
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
