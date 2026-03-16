"""Tests for Sense Intelligence protocol."""

import json
from pathlib import Path

from syke.sense.intelligence import SenseIntelligence


def test_full_protocol_end_to_end(tmp_path):
    harness_dir = tmp_path / ".test-harness"
    harness_dir.mkdir()
    data_file = harness_dir / "sessions.jsonl"
    lines = [
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "role": "user",
                "content": f"msg {i}",
            }
        )
        for i in range(5)
    ]
    data_file.write_text("\n".join(lines) + "\n")

    si = SenseIntelligence(home=tmp_path)
    result = si.connect(harness_dir)
    assert result.source_name == "test-harness"


def test_connect_missing_path(tmp_path):
    si = SenseIntelligence(home=tmp_path)
    result = si.connect(tmp_path / "nonexistent")
    assert not result.success
    assert "not found" in result.message


def test_setup_discover_finds_harnesses(tmp_path):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "test.jsonl").write_text('{"test": 1}\n')
    si = SenseIntelligence(home=tmp_path)
    results = si.discover()
    assert len(results) >= 1
    assert any(r.source_name == "claude-code" for r in results)


def test_heal_regenerates(tmp_path):
    samples = [
        json.dumps(
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "role": "user",
                "content": "test",
            }
        )
    ]
    si = SenseIntelligence(home=tmp_path)
    result = si.heal("broken-source", samples)
    assert result.source_name == "broken-source"
