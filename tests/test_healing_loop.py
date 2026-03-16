"""Tests for HealingLoop health scoring and adapter regeneration trigger."""

from __future__ import annotations

import time
from pathlib import Path
from syke.db import SykeDB
from syke.sense.healing import HealingLoop, SourceHealth
from syke.sense.self_observe import SykeObserver


def test_failure_counting():
    loop = HealingLoop(threshold=0.3, sustained_minutes=0)
    for i in range(4):
        loop.record_failure("test-source", f"bad line {i}")
    assert loop.get_failure_count("test-source") == 4


def test_success_resets_trigger_state():
    loop = HealingLoop(threshold=0.3, sustained_minutes=0)
    for i in range(3):
        loop.record_failure("test-source", f"bad line {i}")
    assert loop.get_failure_count("test-source") == 3
    loop.record_success("test-source")
    # Success clears samples and resets trigger, but error_count is cumulative
    assert loop.get_samples("test-source") == []
    # Health score should improve after success (last_success_time updated)
    assert loop.get_score("test-source") > 0.0


def test_sample_accumulation():
    loop = HealingLoop(threshold=0.01, max_samples=5, sustained_minutes=0)
    for i in range(10):
        loop.record_failure("test-source", f"line {i}")
    samples = loop.get_samples("test-source")
    assert len(samples) == 5
    assert samples[0] == "line 0"


def test_threshold_triggers_healing():
    triggered = []

    def on_threshold(source, samples):
        triggered.append((source, len(samples)))

    loop = HealingLoop(threshold=0.3, on_threshold=on_threshold, sustained_minutes=0)
    for i in range(5):
        loop.record_failure("src", f"line {i}")

    # With sustained_minutes=0 and 100% error rate, score should be very low
    # and trigger immediately after below_threshold_since is set.
    # First failure sets below_threshold_since, subsequent failures check elapsed.
    assert len(triggered) == 1
    assert triggered[0][0] == "src"


def test_healing_emits_self_obs(tmp_path):
    db = SykeDB(tmp_path / "test.db")
    observer = SykeObserver(db=db, user_id="test")
    loop = HealingLoop(observer=observer, threshold=0.3, sustained_minutes=0)
    for i in range(5):
        loop.record_failure("src", f"bad{i}")

    row = db.conn.execute(
        "SELECT event_type FROM events WHERE source='syke' AND event_type='healing.triggered'"
    ).fetchone()
    assert row is not None
    db.close()


def test_health_score_perfect():
    h = SourceHealth()
    h.total_lines = 100
    h.parsed_lines = 100
    h.error_count = 0
    h.columns_seen = {
        "session_id",
        "role",
        "content",
        "event_type",
        "timestamp",
        "tool_name",
        "model",
        "input_tokens",
        "output_tokens",
        "parent_session_id",
    }
    h.event_types_seen = {"turn", "tool_call", "tool_result", "session.start"}
    h.last_success_time = time.time()
    assert h.score() > 0.9


def test_health_score_terrible():
    h = SourceHealth()
    h.total_lines = 100
    h.error_count = 100
    assert h.score() < 0.2
