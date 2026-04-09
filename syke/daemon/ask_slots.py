"""Process-level counting semaphore for concurrent Pi ask processes.

Uses a directory of PID files (~/.config/syke/ask-slots/) as a cross-process
counting semaphore.  Self-healing: stale PID files (from crashed processes)
are cleaned up on every acquire attempt.

Only gates *cold* Pi fallback spawns — the daemon warm runtime is already
serialized by its own _runtime_lock and doesn't count against this limit.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SLOT_DIR = Path(os.path.expanduser("~/.config/syke/ask-slots"))

# Default: 4 concurrent cold Pi processes.
# Override via config.toml [ask] max_parallel or SYKE_MAX_PARALLEL_ASKS env var.
DEFAULT_MAX_PARALLEL = 8


def _cleanup_stale(slot_dir: Path) -> int:
    """Remove slot files whose PIDs are no longer running. Returns count removed."""
    removed = 0
    if not slot_dir.is_dir():
        return 0
    for slot_file in slot_dir.iterdir():
        if not slot_file.name.isdigit():
            continue
        pid = int(slot_file.name)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            # Process is dead — reclaim the slot.
            try:
                slot_file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
        except PermissionError:
            # Process exists but owned by another user — leave it.
            pass
        except OSError:
            # Unexpected — remove to be safe.
            try:
                slot_file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def _count_active(slot_dir: Path) -> int:
    """Count active slot files (live PIDs)."""
    if not slot_dir.is_dir():
        return 0
    count = 0
    for slot_file in slot_dir.iterdir():
        if slot_file.name.isdigit():
            count += 1
    return count


def acquire(
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    timeout: float = 30.0,
    slot_dir: Path | None = None,
) -> bool:
    """Acquire a slot for a cold Pi ask process.

    Blocks up to *timeout* seconds waiting for a slot.  Returns True if
    acquired, False if timed out.  The slot is released by calling
    :func:`release` (or automatically when the process exits, after the
    next :func:`_cleanup_stale` pass).
    """
    if max_parallel <= 0:
        return True  # unlimited

    dir_ = slot_dir or SLOT_DIR
    dir_.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    slot_file = dir_ / str(pid)

    # Fast path: if we already hold a slot, don't double-count.
    if slot_file.exists():
        return True

    deadline = time.monotonic() + timeout
    poll_interval = 0.25

    while True:
        _cleanup_stale(dir_)
        active = _count_active(dir_)

        if active < max_parallel:
            try:
                slot_file.write_text(str(pid), encoding="utf-8")
                logger.debug(
                    "ask slot acquired (pid=%d, active=%d/%d)", pid, active + 1, max_parallel
                )
                return True
            except OSError as exc:
                logger.warning("Failed to write ask slot file: %s", exc)
                return False

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "ask slot timeout (pid=%d, active=%d/%d, waited=%.1fs)",
                pid,
                active,
                max_parallel,
                timeout,
            )
            return False

        time.sleep(min(poll_interval, remaining))
        # Back off slightly on contention.
        poll_interval = min(poll_interval * 1.5, 2.0)


def release(slot_dir: Path | None = None) -> None:
    """Release the slot held by this process."""
    dir_ = slot_dir or SLOT_DIR
    slot_file = dir_ / str(os.getpid())
    try:
        slot_file.unlink(missing_ok=True)
    except OSError:
        pass


def active_count(slot_dir: Path | None = None) -> int:
    """Return number of currently active ask slots (after cleanup)."""
    dir_ = slot_dir or SLOT_DIR
    _cleanup_stale(dir_)
    return _count_active(dir_)
