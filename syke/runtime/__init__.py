"""
Pi agent runtime management.

Provides singleton lifecycle for the persistent Pi process,
managed by the Syke daemon.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from syke.llm.pi_client import PiRuntime

logger = logging.getLogger(__name__)

_runtime: PiRuntime | None = None


def get_pi_runtime() -> PiRuntime:
    """Get the active Pi runtime instance. Raises if not started."""
    if _runtime is None:
        raise RuntimeError(
            "Pi runtime not initialized. "
            "Start the daemon with runtime='pi' or call start_pi_runtime()."
        )
    return _runtime


def start_pi_runtime(
    workspace_dir: str | Path,
    session_dir: str | Path | None = None,
    model: str | None = None,
) -> PiRuntime:
    """Initialize and start the singleton Pi runtime."""
    global _runtime
    from syke.llm.pi_client import PiRuntime as _PiRuntime, resolve_pi_model

    requested_model = resolve_pi_model(model)

    if _runtime and _runtime.is_alive:
        if _runtime.model == requested_model:
            logger.info("Pi runtime already running, returning existing instance")
            return _runtime

        logger.info(
            "Pi runtime model change requested (%s → %s), restarting runtime",
            _runtime.model,
            requested_model,
        )
        _runtime.stop()
        _runtime = None

    _runtime = _PiRuntime(
        workspace_dir=workspace_dir,
        session_dir=session_dir,
        model=model,
    )
    _runtime.start()
    return _runtime

def stop_pi_runtime() -> None:
    """Stop the singleton Pi runtime."""
    global _runtime
    if _runtime is not None:
        _runtime.stop()
        _runtime = None
        logger.info("Pi runtime stopped and cleared")
