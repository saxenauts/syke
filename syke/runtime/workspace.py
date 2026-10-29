"""Materialized Syke root, control, and controller-workspace boundaries."""

from __future__ import annotations

import os
from pathlib import Path

from syke.config import SYKE_HOME

SYKE_ROOT = SYKE_HOME
CONTROL_ROOT = Path(
    os.path.expanduser(os.environ.get("SYKE_CONTROL_ROOT", str(SYKE_ROOT / "control")))
).resolve()
WORKSPACE_ROOT = Path(
    os.path.expanduser(os.environ.get("SYKE_WORKSPACE_ROOT", str(SYKE_ROOT / "workspace")))
).resolve()

RUNTIME_DIR = CONTROL_ROOT / "runtime"
TEMP_DIR = RUNTIME_DIR / "tmp"
TOKENIZER_CACHE_DIR = CONTROL_ROOT / "tokenizers"

# Native Pi operation history is written by the trusted runtime, not the controller.
SESSIONS_DIR = CONTROL_ROOT / "sessions"

# Host-written outcomes and one-way external inputs are protected from the controller.
RECEIPTS_DIR = CONTROL_ROOT / "receipts"
RECORDS_DIR = CONTROL_ROOT / "records"

# Canonical learned-memory database
SYKE_DB = WORKSPACE_ROOT / "syke.db"

# Memex projected from canonical memory
MEMEX_PATH = WORKSPACE_ROOT / "MEMEX.md"

# Controller-maintained artifacts and harness candidates
ARTIFACTS_DIR = WORKSPACE_ROOT / "artifacts"
HARNESS_DIR = WORKSPACE_ROOT / "harness"
SCRATCH_DIR = WORKSPACE_ROOT / "scratch"


def initialize_workspace(*, selected_sources: tuple[str, ...] | None = None) -> None:
    """Create the current owned workspace and install source readers.

    Called once at setup/daemon startup. Creates dirs, installs adapter
    markdowns from seeds. Idempotent.

    MEMEX.md is NOT written here — synthesis owns MEMEX creation.
    syke.db is NOT created here — SykeDB constructor handles that.
    """
    import logging

    logger = logging.getLogger(__name__)

    from syke.memory.memex_budget import warm_memex_tokenizer

    CONTROL_ROOT.mkdir(parents=True, exist_ok=True)
    for path in (
        SESSIONS_DIR,
        RECEIPTS_DIR,
        RECORDS_DIR,
        RUNTIME_DIR,
        TEMP_DIR,
        TOKENIZER_CACHE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
    for path in (
        WORKSPACE_ROOT,
        ARTIFACTS_DIR,
        HARNESS_DIR,
        SCRATCH_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
    warm_memex_tokenizer()

    from syke.observe.bootstrap import ensure_adapters

    ensure_adapters(WORKSPACE_ROOT, selected_sources=selected_sources)

    logger.debug("Syke root initialized: control=%s workspace=%s", CONTROL_ROOT, WORKSPACE_ROOT)
