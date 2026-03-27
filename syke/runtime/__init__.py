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
_runtime_key: tuple[str, str, str] | None = None


def _normalize_runtime_key(
    workspace_dir: str | Path,
    session_dir: str | Path | None,
    model: str | None,
) -> tuple[str, str, str]:
    from syke.llm.pi_client import resolve_pi_model

    workspace_path = Path(workspace_dir).expanduser().resolve()
    session_path = (
        Path(session_dir).expanduser().resolve()
        if session_dir is not None
        else (workspace_path / "sessions").resolve()
    )
    return (str(workspace_path), str(session_path), resolve_pi_model(model))


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
    global _runtime, _runtime_key
    from syke.llm.pi_client import PiRuntime as _PiRuntime, resolve_pi_model

    requested_key = _normalize_runtime_key(workspace_dir, session_dir, model)
    requested_model = resolve_pi_model(model)

    if _runtime and _runtime.is_alive:
        if _runtime_key == requested_key:
            logger.info("Pi runtime already running, returning existing instance")
            return _runtime

        logger.info(
            "Pi runtime binding change requested (%s -> %s), restarting runtime",
            _runtime_key,
            requested_key,
        )
        _runtime.stop()
        _runtime = None
        _runtime_key = None

    _runtime = _PiRuntime(
        workspace_dir=workspace_dir,
        session_dir=session_dir,
        model=model,
    )
    _runtime.start()
    _runtime_key = requested_key
    return _runtime

def stop_pi_runtime() -> None:
    """Stop the singleton Pi runtime."""
    global _runtime, _runtime_key
    if _runtime is not None:
        _runtime.stop()
        _runtime = None
        _runtime_key = None
        logger.info("Pi runtime stopped and cleared")
