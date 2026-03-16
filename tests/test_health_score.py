"""Tests for SourceHealth scoring and HealingLoop deterministic triggers."""

from __future__ import annotations

import time

from syke.sense.healing import CANONICAL_COLUMNS, HealingLoop, SourceHealth


def test_column_completeness_empty():
    h = SourceHealth()
    assert h.column_completeness == 0.0


def test_column_completeness_half():
    h = SourceHealth()
    h.columns_seen = {"session_id", "role", "content", "event_type", "timestamp"}
    assert h.column_completeness == 0.5


def test_column_completeness_full():
    h = SourceHealth()
    h.columns_seen = set(CANONICAL_COLUMNS)
    assert h.column_completeness == 1.0


def test_event_granularity_zero():
    h = SourceHealth()
    assert h.event_granularity == 0.0


def test_event_granularity_one():
    h = SourceHealth()
    h.event_types_seen = {"turn"}
    assert h.event_granularity == 0.2


def test_event_granularity_two():
    h = SourceHealth()
    h.event_types_seen = {"turn", "tool_call"}
    assert h.event_granularity == 0.6


def test_event_granularity_four():
    h = SourceHealth()
    h.event_types_seen = {"turn", "tool_call", "tool_result", "session.start"}
    assert h.event_granularity == 1.0


def test_error_rate_zero():
    h = SourceHealth()
    h.total_lines = 100
    h.error_count = 0
    assert h.error_rate == 0.0


def test_error_rate_half():
    h = SourceHealth()
    h.total_lines = 100
    h.error_count = 50
    assert h.error_rate == 0.5


def test_error_rate_empty():
    h = SourceHealth()
    assert h.error_rate == 0.0


def test_schema_drift_none():
    h = SourceHealth()
    assert h.schema_drift == 0.0


def test_schema_drift_some():
    h = SourceHealth()
    h.extras_keys_seen = {"session_id", "custom_field", "another"}
    drift = h.schema_drift
    assert 0.0 < drift < 1.0


def test_freshness_now():
    h = SourceHealth()
    h.last_success_time = time.time()
    assert h.freshness == 1.0


def test_freshness_old():
    h = SourceHealth()
    h.last_success_time = time.time() - 200000
    assert h.freshness == 0.0


def test_freshness_never():
    h = SourceHealth()
    assert h.freshness == 0.0


def test_score_perfect():
    h = SourceHealth()
    h.total_lines = 100
    h.parsed_lines = 100
    h.columns_seen = set(CANONICAL_COLUMNS)
    h.event_types_seen = {"turn", "tool_call", "tool_result", "session.start"}
    h.last_success_time = time.time()
    assert h.score() > 0.9


def test_score_terrible():
    h = SourceHealth()
    h.total_lines = 100
    h.error_count = 100
    assert h.score() < 0.2


def test_score_mixed():
    h = SourceHealth()
    h.total_lines = 20
    h.error_count = 5
    h.parsed_lines = 15
    h.columns_seen = {"session_id", "role", "content"}
    h.event_types_seen = {"turn"}
    h.last_success_time = time.time()
    score = h.score()
    assert 0.2 < score < 0.8


def test_loop_record_failure_updates():
    loop = HealingLoop(threshold=0.3, sustained_minutes=999)
    loop.record_failure("src", "bad1")
    loop.record_failure("src", "bad2")
    assert loop.get_failure_count("src") == 2
    assert len(loop.get_samples("src")) == 2


def test_loop_record_success_clears_samples():
    loop = HealingLoop(threshold=0.3, sustained_minutes=999)
    loop.record_failure("src", "bad1")
    loop.record_success("src", {"event_type": "turn", "role": "user", "content": "hi"})
    assert loop.get_samples("src") == []


def test_loop_success_tracks_columns():
    loop = HealingLoop(threshold=0.3, sustained_minutes=0)
    loop.record_success(
        "src",
        {
            "session_id": "s1",
            "role": "user",
            "content": "hi",
            "event_type": "turn",
            "timestamp": "2026-01-01",
        },
    )
    h = loop.get_health("src")
    assert h is not None
    assert "session_id" in h.columns_seen
    assert "turn" in h.event_types_seen


def test_loop_threshold_triggers(tmp_path):
    triggered = []
    loop = HealingLoop(
        threshold=0.3,
        on_threshold=lambda s, samples: triggered.append(s),
        sustained_minutes=0,
    )
    for i in range(10):
        loop.record_failure("bad-src", f"garbage line {i}")
    assert "bad-src" in triggered


def test_loop_no_double_trigger():
    triggered = []
    loop = HealingLoop(
        threshold=0.3,
        on_threshold=lambda s, samples: triggered.append(s),
        sustained_minutes=0,
    )
    for i in range(20):
        loop.record_failure("src", f"line {i}")
    assert triggered.count("src") == 1


def test_loop_recovery_prevents_trigger():
    triggered = []
    loop = HealingLoop(
        threshold=0.3,
        on_threshold=lambda s, samples: triggered.append(s),
        sustained_minutes=999,
    )
    loop.record_failure("src", "bad1")
    loop.record_success("src", {"event_type": "turn", "role": "user"})
    assert "src" not in triggered


def test_loop_get_score():
    loop = HealingLoop(threshold=0.3, sustained_minutes=0)
    assert loop.get_score("unknown") == 1.0
    loop.record_failure("src", "bad")
    assert loop.get_score("src") < 1.0
