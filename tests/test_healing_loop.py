"""Tests for HealingLoop failure detection and sample accumulation."""

from __future__ import annotations

from pathlib import Path
from syke.db import SykeDB
from syke.sense.healing import HealingLoop
from syke.sense.self_observe import SykeObserver


def test_failure_counting():
    loop = HealingLoop(threshold=5)
    for i in range(4):
        loop.record_failure("test-source", f"bad line {i}")
    assert loop.get_failure_count("test-source") == 4


def test_success_resets():
    loop = HealingLoop(threshold=5)
    for i in range(3):
        loop.record_failure("test-source", f"bad line {i}")
    loop.record_success("test-source")
    assert loop.get_failure_count("test-source") == 0
    assert loop.get_samples("test-source") == []


def test_sample_accumulation():
    loop = HealingLoop(threshold=100, max_samples=5)
    for i in range(10):
        loop.record_failure("test-source", f"line {i}")
    samples = loop.get_samples("test-source")
    assert len(samples) == 5  # capped at max_samples
    assert samples[0] == "line 0"


def test_threshold_triggers_healing():
    triggered = []

    def on_threshold(source, samples):
        triggered.append((source, len(samples)))

    loop = HealingLoop(threshold=3, on_threshold=on_threshold)
    for i in range(3):
        loop.record_failure("src", f"line {i}")
    assert len(triggered) == 1
    assert triggered[0] == ("src", 3)


def test_healing_emits_self_obs(tmp_path):
    db = SykeDB(tmp_path / "test.db")
    observer = SykeObserver(db=db, user_id="test")
    loop = HealingLoop(observer=observer, threshold=2)
    loop.record_failure("src", "bad1")
    loop.record_failure("src", "bad2")

    row = db.conn.execute(
        "SELECT event_type FROM events WHERE source='syke' AND event_type='healing.triggered'"
    ).fetchone()
    assert row is not None
    db.close()
