"""Tests for Sense analyzer."""

import json
from syke.sense.analyzer import SenseAnalyzer


def test_analyzer_detects_jsonl():
    samples = [
        json.dumps(
            {
                "type": "human",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"content": [{"text": "hi"}]},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {"content": [{"text": "hello"}]},
            }
        ),
    ]
    result = SenseAnalyzer().analyze(samples)
    assert result.format == "jsonl"


def test_analyzer_detects_sqlite():
    samples = ["SQLite format 3\x00"]
    result = SenseAnalyzer().analyze(samples)
    assert result.format == "sqlite"


def test_analyzer_identifies_turns():
    samples = [
        json.dumps({"role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00Z"}),
    ]
    result = SenseAnalyzer().analyze(samples)
    assert result.role_field == "role"
    assert result.content_field == "content"


def test_analyzer_identifies_timestamps():
    samples = [
        json.dumps({"timestamp": "2026-01-01T00:00:00Z", "data": "test"}),
    ]
    result = SenseAnalyzer().analyze(samples)
    assert result.timestamp_field == "timestamp"
