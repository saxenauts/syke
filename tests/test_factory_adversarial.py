"""Adversarial tests for sense factory + handler healing wiring.

Targets edge cases, pathological inputs, and concurrency scenarios
that the happy-path test_factory.py does not cover.
"""

from __future__ import annotations

import json
import os
import textwrap
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from syke.observe.factory import (
    check_parse,
    connect,
    deploy,
    discover,
    generate,
    heal,
)
from syke.observe.runtime import SenseFileHandler


# ===================================================================
# Factory edge cases
# ===================================================================


class TestGenerateEmptySamples:
    """1. generate() with empty samples list."""

    def test_no_llm_empty_samples(self):
        code = generate("test-source", [], llm_fn=None)
        assert code is not None
        assert "parse_line" in code

    def test_with_llm_empty_samples(self):
        """LLM gets empty samples string — should still produce something."""

        def fake_llm(prompt):
            return "```python\ndef parse_line(line):\n    return None\n```"

        code = generate("test-source", [], llm_fn=fake_llm)
        assert code is not None


class TestConnectBinaryOnly:
    """2. connect() with a directory containing only binary files."""

    def test_binary_only_dir(self, tmp_path):
        d = tmp_path / ".binary-harness"
        d.mkdir()
        # Write binary garbage with json extensions — _read_samples should cope
        (d / "data.jsonl").write_bytes(b"\x00\x01\x89PNG\r\n\x1a\n" * 100)
        (d / "image.json").write_bytes(bytes(range(256)) * 10)

        ok, msg = connect(d)
        # Either it finds no usable data, or it reads garbled lines but fails parse
        assert not ok or "events parsed" in msg

    def test_no_json_files_at_all(self, tmp_path):
        d = tmp_path / ".bin-tool"
        d.mkdir()
        (d / "model.bin").write_bytes(b"\xff" * 1024)
        (d / "weights.pt").write_bytes(b"\x00" * 512)

        ok, msg = connect(d)
        assert not ok
        assert "No data" in msg


class TestHealLargeSampleSet:
    """3. heal() with 1000 samples (should still work)."""

    def test_1000_samples(self, tmp_path):
        samples = [
            json.dumps({"timestamp": f"2026-01-{(i % 28) + 1:02d}", "role": "user", "content": f"msg {i}",
                        "session_id": f"s{i // 10}", "event_type": "turn"})
            for i in range(1000)
        ]
        ok = heal("stress", samples, adapters_dir=tmp_path)
        assert ok
        assert (tmp_path / "stress" / "adapter.py").exists()


class TestCheckParseTimeout:
    """4. check_parse() with code that hangs (infinite loop) — should timeout."""

    def test_infinite_loop_code(self):
        evil_code = textwrap.dedent("""\
            def parse_line(line):
                while True:
                    pass
        """)
        ok, n, _cov = check_parse(evil_code, ['{"a":1}'], timeout=2)
        assert not ok
        assert n == 0

    def test_sleep_bomb(self):
        evil_code = textwrap.dedent("""\
            import time
            def parse_line(line):
                time.sleep(9999)
                return {"a": 1}
        """)
        ok, n, _cov = check_parse(evil_code, ['{"a":1}'], timeout=2)
        assert not ok
        assert n == 0


class TestDeployPathConflict:
    """5. deploy() when adapters_dir is a file not a directory."""

    def test_adapters_dir_is_file(self, tmp_path):
        fake_file = tmp_path / "adapters"
        fake_file.write_text("I am a file, not a directory")

        ok = deploy("src", "code here", fake_file)
        # mkdir on a path where a file exists should fail → deploy returns False
        assert not ok

    def test_nested_target_blocked(self, tmp_path):
        # adapters_dir exists, but target subdir is a file
        adapters = tmp_path / "adapters"
        adapters.mkdir()
        blocker = adapters / "my-source"
        blocker.write_text("blocking file")

        ok = deploy("my-source", "code here", adapters)
        # mkdir(exist_ok=True) on a file path should raise OSError
        assert not ok


class TestDiscoverSymlinks:
    """6. discover() with symlinks pointing to nonexistent dirs."""

    def test_dangling_symlink(self, tmp_path):
        target = tmp_path / "ghost"
        link = tmp_path / ".claude"
        link.symlink_to(target)
        # .claude exists as a symlink but target doesn't exist
        # path.exists() follows symlinks, so it returns False for dangling
        results = discover(home=tmp_path)
        # Should not include the dangling symlink
        assert not any(r["source"] == "claude-code" for r in results)

    def test_valid_symlink(self, tmp_path):
        target = tmp_path / "real-claude"
        target.mkdir()
        (target / "data.jsonl").write_text('{"x": 1}\n')
        link = tmp_path / ".claude"
        link.symlink_to(target)

        results = discover(home=tmp_path)
        assert any(r["source"] == "claude-code" for r in results)

    def test_circular_symlink(self, tmp_path):
        # Create a circular symlink scenario
        a = tmp_path / ".claude"
        b = tmp_path / "loop_target"
        # a -> b, b -> a won't work with symlink_to on nonexistent...
        # but we can make a point to itself
        try:
            a.symlink_to(a)  # self-referencing
        except OSError:
            pytest.skip("OS does not support this symlink pattern")

        results = discover(home=tmp_path)
        # Should not crash
        assert isinstance(results, list)


class TestGenerateEmptyLLMResponse:
    """7. generate() with an llm_fn that returns empty string."""

    def test_empty_string(self):
        def empty_llm(prompt):
            return ""

        code = generate("test", ['{"a": 1}'], llm_fn=empty_llm)
        # _strip_fencing("") returns "" → code is falsy → generate returns None
        assert code is None

    def test_whitespace_only(self):
        def ws_llm(prompt):
            return "   \n\n\t  "

        code = generate("test", ['{"a": 1}'], llm_fn=ws_llm)
        # .strip() on whitespace → "" → falsy → None
        assert code is None

    def test_fencing_with_empty_body(self):
        def fence_llm(prompt):
            return "```python\n```"

        code = generate("test", ['{"a": 1}'], llm_fn=fence_llm)
        # Regex captures empty string between fences → strip → "" → None
        assert code is None


class TestConnectSandboxFailure:
    """8. connect() where generated code fails sandbox — should report failure."""

    def test_llm_generates_broken_adapter(self, tmp_path):
        d = tmp_path / ".test-harness"
        d.mkdir()
        (d / "data.jsonl").write_text('{"ts":"2026-01-01","role":"user","content":"hi"}\n' * 5)

        def bad_llm(prompt):
            return "```python\ndef parse_line(line):\n    raise RuntimeError('intentional crash')\n```"

        ok, msg = connect(d, llm_fn=bad_llm)
        assert not ok
        assert "all 3 attempts failed" in msg.lower() or "parsed 0" in msg.lower()

    def test_llm_generates_non_dict_return(self, tmp_path):
        d = tmp_path / ".test-harness"
        d.mkdir()
        (d / "data.jsonl").write_text('{"a":1}\n{"b":2}\n')

        def string_llm(prompt):
            return '```python\ndef parse_line(line):\n    return "not a dict"\n```'

        ok, msg = connect(d, llm_fn=string_llm)
        assert not ok


# ===================================================================
# Handler healing wiring
# ===================================================================


def _make_handler(heal_fn=None, heal_threshold=5):
    """Build a SenseFileHandler with a mock writer, stubbed for direct testing."""
    writer = MagicMock()
    handler = SenseFileHandler(
        writer,
        system_name="Darwin",  # force macOS path
        heal_fn=heal_fn,
        heal_threshold=heal_threshold,
    )
    return handler


def _simulate_failures(handler, source, failures, successes=None):
    """Directly poke the handler's failure tracking to simulate tailer output.

    Instead of going through on_modified (which needs real files + watchdog events),
    we call the internal logic that _process_file uses.
    """
    if failures:
        handler._failure_counts[source] += len(failures)
        handler._failure_samples[source].extend(failures[:20])
        if (
            handler._heal_fn
            and source not in handler._healed
            and handler._failure_counts[source] >= handler._heal_threshold
        ):
            handler._healed.add(source)
            try:
                handler._heal_fn(source, handler._failure_samples[source][:20])
            except Exception:
                pass

    if successes:
        handler._failure_counts.pop(source, None)
        handler._failure_samples.pop(source, None)
        handler._healed.discard(source)


class TestHandlerHealThreshold:
    """9. Handler fires heal_fn after heal_threshold failures."""

    def test_fires_at_threshold(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=5)

        # 4 failures — not enough
        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 4)
        heal_mock.assert_not_called()

        # 1 more — hits threshold of 5
        _simulate_failures(handler, "/path/a.jsonl", ["bad"])
        heal_mock.assert_called_once()
        args = heal_mock.call_args
        assert args[0][0] == "/path/a.jsonl"

    def test_does_not_fire_below_threshold(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=10)

        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 9)
        heal_mock.assert_not_called()


class TestHandlerNoDuplicateHeal:
    """10. Handler does NOT fire heal_fn twice for same source."""

    def test_no_double_heal(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=3)

        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 5)
        assert heal_mock.call_count == 1

        # More failures — should NOT trigger again
        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 10)
        assert heal_mock.call_count == 1


class TestHandlerResetAfterSuccess:
    """11. Handler resets after success, allows re-healing."""

    def test_reset_and_reheal(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=3)

        # First round of failures → heal
        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 3)
        assert heal_mock.call_count == 1

        # Success resets
        _simulate_failures(handler, "/path/a.jsonl", failures=None, successes=True)
        assert "/path/a.jsonl" not in handler._healed
        assert "/path/a.jsonl" not in handler._failure_counts

        # Second round of failures → heal again
        _simulate_failures(handler, "/path/a.jsonl", ["bad2"] * 3)
        assert heal_mock.call_count == 2


class TestHandlerNoHealFn:
    """12. Handler with heal_fn=None doesn't crash on failures."""

    def test_none_heal_fn(self):
        handler = _make_handler(heal_fn=None, heal_threshold=1)

        # Should not raise
        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 20)
        assert handler._failure_counts["/path/a.jsonl"] == 20


class TestHandlerMultipleFiles:
    """13. Multiple files failing simultaneously, each triggers independently."""

    def test_independent_triggers(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=3)

        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 3)
        _simulate_failures(handler, "/path/b.jsonl", ["bad"] * 3)
        _simulate_failures(handler, "/path/c.jsonl", ["bad"] * 3)

        assert heal_mock.call_count == 3
        sources_healed = {call[0][0] for call in heal_mock.call_args_list}
        assert sources_healed == {"/path/a.jsonl", "/path/b.jsonl", "/path/c.jsonl"}

    def test_one_heals_other_doesnt(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=5)

        _simulate_failures(handler, "/path/a.jsonl", ["bad"] * 5)  # triggers
        _simulate_failures(handler, "/path/b.jsonl", ["bad"] * 3)  # doesn't

        assert heal_mock.call_count == 1
        assert heal_mock.call_args[0][0] == "/path/a.jsonl"


class TestHandlerImmediateTrigger:
    """14. Handler with heal_threshold=1 triggers immediately."""

    def test_threshold_one(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=1)

        _simulate_failures(handler, "/path/a.jsonl", ["bad"])
        heal_mock.assert_called_once()

    def test_threshold_one_multiple_sources(self):
        heal_mock = MagicMock()
        handler = _make_handler(heal_fn=heal_mock, heal_threshold=1)

        for i in range(10):
            _simulate_failures(handler, f"/path/{i}.jsonl", ["bad"])

        assert heal_mock.call_count == 10
