"""5.9 — Healing Flow.

Proves: Health scoring drops on parse failures, on_threshold callback
fires after sustained low score, and record_success resets trigger state.
"""

from __future__ import annotations

import time

from syke.sense.healing import HealingLoop, SourceHealth


def test_format_change_drops_health():
    loop = HealingLoop(threshold=0.3, sustained_minutes=999)

    for _ in range(5):
        loop.record_success(
            "src",
            {
                "session_id": "s1",
                "role": "user",
                "content": "ok",
                "event_type": "turn",
                "timestamp": "2026-01-01",
            },
        )

    healthy = loop.get_score("src")
    assert healthy > 0.5

    for i in range(20):
        loop.record_failure("src", f"garbage line {i}")

    degraded = loop.get_score("src")
    assert degraded < healthy


def test_sustained_low_score_fires_callback():
    triggered = []

    def on_threshold(source, samples):
        triggered.append((source, len(samples)))

    loop = HealingLoop(
        threshold=0.3,
        on_threshold=on_threshold,
        sustained_minutes=0,
    )
    for i in range(10):
        loop.record_failure("broken-src", f"bad line {i}")

    assert len(triggered) == 1
    assert triggered[0][0] == "broken-src"


def test_success_resets_trigger_state():
    triggered = []

    def on_threshold(source, samples):
        triggered.append(source)

    loop = HealingLoop(
        threshold=0.3,
        on_threshold=on_threshold,
        sustained_minutes=0,
    )

    for i in range(10):
        loop.record_failure("src", f"bad {i}")
    assert triggered.count("src") == 1

    loop.record_success("src", {"event_type": "turn", "role": "user"})
    h = loop.get_health("src")
    assert h is not None
    assert h.below_threshold_since == 0.0
    assert "src" not in loop._triggered


def test_health_score_components():
    h = SourceHealth()
    h.total_lines = 50
    h.parsed_lines = 45
    h.error_count = 5
    h.columns_seen = {"session_id", "role", "content", "event_type", "timestamp"}
    h.event_types_seen = {"turn", "tool_call"}
    h.last_success_time = time.time()

    score = h.score()
    assert 0.4 < score < 0.9

    assert h.column_completeness == 0.5
    assert h.event_granularity == 0.6
    assert h.error_rate == 0.1
    assert h.freshness == 1.0


def test_full_heal_cycle():
    healed = []

    def on_threshold(source, samples):
        healed.append(source)

    loop = HealingLoop(
        threshold=0.3,
        on_threshold=on_threshold,
        sustained_minutes=0,
    )

    for i in range(15):
        loop.record_failure("bad-adapter", f"corrupt line {i}")
    assert "bad-adapter" in healed

    for _ in range(10):
        loop.record_success(
            "bad-adapter",
            {
                "session_id": "s1",
                "role": "user",
                "content": "hi",
                "event_type": "turn",
                "timestamp": "2026-03-16T00:00:00Z",
                "tool_name": "bash",
                "model": "kimi",
            },
        )

    score_after = loop.get_score("bad-adapter")
    assert score_after > 0.3
