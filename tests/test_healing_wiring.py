"""Tests for healing loop wiring into the watcher parse pipeline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from syke.sense.handler import SenseFileHandler
from syke.sense.healing import HealingLoop
from syke.sense.writer import SenseWriter


@pytest.fixture
def temp_jsonl_file() -> Path:
    """Create a temporary JSONL file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        temp_path = Path(f.name)
    yield temp_path
    temp_path.unlink(missing_ok=True)


@pytest.fixture
def mock_writer() -> SenseWriter:
    """Create a mock SenseWriter."""
    writer = MagicMock(spec=SenseWriter)
    writer.enqueue = MagicMock()
    return writer


def test_parse_failure_routes_to_healing(temp_jsonl_file: Path, mock_writer: SenseWriter) -> None:
    """Test that parse failures are routed to healing.record_failure()."""
    healing = HealingLoop(threshold=2)
    handler = SenseFileHandler(mock_writer, healing=healing, system_name="Linux")

    bad_json_lines = [
        '{"valid": "json"}\n',
        "not valid json\n",
        '{"another": "valid"}\n',
        "also not json\n",
    ]

    with temp_jsonl_file.open("w") as f:
        f.writelines(bad_json_lines)

    handler._process_file(temp_jsonl_file)

    failure_count = healing.get_failure_count(str(temp_jsonl_file))
    assert failure_count == 2, f"Expected 2 failures, got {failure_count}"

    samples = healing.get_samples(str(temp_jsonl_file))
    assert len(samples) == 2, f"Expected 2 samples, got {len(samples)}"
    assert "not valid json" in samples[0]
    assert "also not json" in samples[1]


def test_healing_callback_invoked(temp_jsonl_file: Path, mock_writer: SenseWriter) -> None:
    """Test that on_threshold callback fires when failure count reaches threshold."""
    callback_invoked = {"called": False, "source": None, "samples": None}

    def on_threshold_callback(source: str, samples: list[str]) -> None:
        callback_invoked["called"] = True
        callback_invoked["source"] = source
        callback_invoked["samples"] = samples

    healing = HealingLoop(threshold=3, on_threshold=on_threshold_callback)
    handler = SenseFileHandler(mock_writer, healing=healing, system_name="Linux")

    bad_json_lines = [
        '{"valid": "json"}\n',
        "bad1\n",
        "bad2\n",
        "bad3\n",
    ]

    with temp_jsonl_file.open("w") as f:
        f.writelines(bad_json_lines)

    handler._process_file(temp_jsonl_file)

    assert callback_invoked["called"], "on_threshold callback was not invoked"
    assert callback_invoked["source"] == str(temp_jsonl_file)
    assert len(callback_invoked["samples"]) == 3
    assert "bad1" in callback_invoked["samples"][0]
    assert "bad2" in callback_invoked["samples"][1]
    assert "bad3" in callback_invoked["samples"][2]


def test_successful_parse_records_success(temp_jsonl_file: Path, mock_writer: SenseWriter) -> None:
    """Test that successful parses record success and reset failure count."""
    healing = HealingLoop(threshold=2)
    handler = SenseFileHandler(mock_writer, healing=healing, system_name="Linux")

    with temp_jsonl_file.open("w") as f:
        f.write('{"valid": "json"}\n')

    handler._process_file(temp_jsonl_file)

    failure_count = healing.get_failure_count(str(temp_jsonl_file))
    assert failure_count == 0, f"Expected 0 failures after success, got {failure_count}"


def test_mixed_valid_and_invalid_lines(temp_jsonl_file: Path, mock_writer: SenseWriter) -> None:
    """Test handling of mixed valid and invalid JSON lines."""
    healing = HealingLoop(threshold=5)
    handler = SenseFileHandler(mock_writer, healing=healing, system_name="Linux")

    lines = [
        '{"id": 1}\n',
        "invalid\n",
        '{"id": 2}\n',
        "also invalid\n",
        '{"id": 3}\n',
    ]

    with temp_jsonl_file.open("w") as f:
        f.writelines(lines)

    handler._process_file(temp_jsonl_file)

    failure_count = healing.get_failure_count(str(temp_jsonl_file))
    assert failure_count == 2, f"Expected 2 failures, got {failure_count}"

    assert mock_writer.enqueue.call_count == 3, "Expected 3 valid records to be enqueued"
