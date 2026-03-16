"""Tests for Sense discovery."""

from pathlib import Path
from syke.sense.discovery import SenseDiscovery


def test_discovery_finds_known_harness(tmp_path):
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    (tmp_path / ".claude" / "projects" / "test.jsonl").write_text('{"test": 1}\n')
    disc = SenseDiscovery(home=tmp_path)
    results = disc.scan()
    known = [r for r in results if r.source_name == "claude-code"]
    assert len(known) == 1
    assert known[0].format_guess == "jsonl"


def test_discovery_flags_unknown(tmp_path):
    (tmp_path / ".aider-new-tool").mkdir()
    (tmp_path / ".aider-new-tool" / "log.json").write_text("{}")
    disc = SenseDiscovery(home=tmp_path)
    results = disc.scan()
    unknown = [r for r in results if r.status == "unknown"]
    assert len(unknown) == 1


def test_discovery_handles_missing_dirs(tmp_path):
    disc = SenseDiscovery(home=tmp_path)
    results = disc.scan()
    assert results == []
