"""Tests for the ask slot semaphore (syke.daemon.ask_slots)."""

import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from syke.daemon.ask_slots import (
    _cleanup_stale,
    _count_active,
    acquire,
    active_count,
    release,
)


def _parallel_acquire_worker(slot_dir: str, start_event, results) -> None:
    slot_path = os.path.abspath(slot_dir)
    start_event.wait()
    got = acquire(max_parallel=1, timeout=0.2, slot_dir=Path(slot_path))
    if got:
        try:
            time.sleep(0.8)
        finally:
            release(slot_dir=Path(slot_path))
    results.put(got)


@pytest.fixture()
def slot_dir(tmp_path):
    d = tmp_path / "ask-slots"
    d.mkdir()
    return d


# --- _cleanup_stale ---


def test_cleanup_removes_dead_pid(slot_dir):
    # Write a slot file for a PID that definitely doesn't exist.
    dead_pid = 2_000_000_000  # implausible
    (slot_dir / str(dead_pid)).write_text(str(dead_pid))
    assert _count_active(slot_dir) == 1
    removed = _cleanup_stale(slot_dir)
    assert removed == 1
    assert _count_active(slot_dir) == 0


def test_cleanup_keeps_live_pid(slot_dir):
    pid = os.getpid()
    (slot_dir / str(pid)).write_text(str(pid))
    removed = _cleanup_stale(slot_dir)
    assert removed == 0
    assert _count_active(slot_dir) == 1


def test_cleanup_ignores_nondigit_files(slot_dir):
    (slot_dir / "README").write_text("not a slot")
    removed = _cleanup_stale(slot_dir)
    assert removed == 0


def test_cleanup_nonexistent_dir(tmp_path):
    removed = _cleanup_stale(tmp_path / "nope")
    assert removed == 0


# --- acquire / release ---


def test_acquire_release_basic(slot_dir):
    assert acquire(max_parallel=2, timeout=1.0, slot_dir=slot_dir)
    assert _count_active(slot_dir) == 1
    release(slot_dir=slot_dir)
    assert _count_active(slot_dir) == 0


def test_acquire_idempotent(slot_dir):
    assert acquire(max_parallel=2, timeout=1.0, slot_dir=slot_dir)
    assert acquire(max_parallel=2, timeout=1.0, slot_dir=slot_dir)
    assert _count_active(slot_dir) == 1  # not double-counted
    release(slot_dir=slot_dir)


def test_acquire_unlimited_when_zero(slot_dir):
    assert acquire(max_parallel=0, timeout=0.1, slot_dir=slot_dir)
    assert _count_active(slot_dir) == 0  # no slot file written


def test_acquire_timeout_when_full(slot_dir):
    # Fill slots with fake live PIDs (use our own PID so they survive cleanup).
    # We can't easily fake multiple live PIDs, so instead we use a child process.
    # Simpler: just write slot files for PIDs that look alive.
    # Use subprocess to hold a real PID.
    procs = []
    for _ in range(3):
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (slot_dir / str(p.pid)).write_text(str(p.pid))
        procs.append(p)

    try:
        # All 3 slots taken, our acquire should timeout.
        got = acquire(max_parallel=3, timeout=0.5, slot_dir=slot_dir)
        assert not got
        assert _count_active(slot_dir) == 3
    finally:
        for p in procs:
            p.terminate()
            p.wait()


def test_acquire_succeeds_after_stale_cleanup(slot_dir):
    # Fill with dead PIDs.
    for i in range(4):
        (slot_dir / str(2_000_000_000 + i)).write_text("dead")
    # Should clean up stale and succeed.
    assert acquire(max_parallel=2, timeout=1.0, slot_dir=slot_dir)
    assert _count_active(slot_dir) == 1


def test_acquire_recovers_stale_lock_file(slot_dir):
    stale_holder_pid = 2_000_000_123
    (slot_dir / ".acquire.lock").write_text(str(stale_holder_pid))

    assert acquire(max_parallel=1, timeout=1.0, slot_dir=slot_dir)
    assert _count_active(slot_dir) == 1

    release(slot_dir=slot_dir)


def test_acquire_is_atomic_under_contention(slot_dir):
    ctx = multiprocessing.get_context("spawn")
    start_event = ctx.Event()
    results = ctx.Queue()
    workers = [
        ctx.Process(target=_parallel_acquire_worker, args=(str(slot_dir), start_event, results))
        for _ in range(6)
    ]

    try:
        for worker in workers:
            worker.start()

        time.sleep(0.3)
        start_event.set()

        acquired = 0
        for _ in workers:
            if results.get(timeout=5):
                acquired += 1

        for worker in workers:
            worker.join(timeout=5)
            assert worker.exitcode == 0

        assert acquired == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=2)


# --- active_count ---


def test_active_count_cleans_and_counts(slot_dir):
    pid = os.getpid()
    dead = 2_000_000_000
    (slot_dir / str(pid)).write_text(str(pid))
    (slot_dir / str(dead)).write_text(str(dead))
    count = active_count(slot_dir=slot_dir)
    assert count == 1  # dead one was cleaned


# --- config integration ---


def test_config_max_parallel_default():
    from syke.config_file import AskConfig

    cfg = AskConfig()
    assert cfg.max_parallel == 8


def test_config_max_parallel_override():
    from syke.config_file import AskConfig

    cfg = AskConfig(max_parallel=8)
    assert cfg.max_parallel == 8
