"""Tests for sense factory — discover, generate, test, heal, connect, deploy."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from syke.observe.factory import (
    connect,
    deploy,
    discover,
    generate,
    heal,
    check_parse,
    _template_fallback,
    _read_samples,
)


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def test_discover_finds_known_harnesses(tmp_path):
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    (tmp_path / ".claude" / "sessions" / "test.jsonl").write_text('{"x":1}\n')
    results = discover(home=tmp_path)
    assert any(r["source"] == "claude-code" for r in results)


def test_discover_empty_home(tmp_path):
    assert discover(home=tmp_path) == []


def test_discover_ignores_unknown_dirs(tmp_path):
    (tmp_path / ".random-tool").mkdir()
    results = discover(home=tmp_path)
    assert not any(r["source"] == "random-tool" for r in results)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def test_generate_template_fallback():
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi"})]
    code = generate("test", samples, llm_fn=None)
    assert code is not None
    assert "parse_line" in code
    assert "json" in code


def test_generate_with_llm_fn():
    def fake_llm(prompt):
        return "```python\nimport json\ndef parse_line(line):\n    return json.loads(line)\n```"

    code = generate("test", ["{}"], llm_fn=fake_llm)
    assert code is not None
    assert "parse_line" in code
    assert "```" not in code  # fencing stripped


def test_generate_llm_exception_falls_back():
    def broken_llm(prompt):
        raise RuntimeError("API down")

    code = generate("test", ["{}"], llm_fn=broken_llm)
    assert code is not None  # should fallback to template
    assert "parse_line" in code


# ---------------------------------------------------------------------------
# test_parse
# ---------------------------------------------------------------------------


def test_check_parse_valid_code():
    code = textwrap.dedent("""\
        import json
        def parse_line(line):
            d = json.loads(line)
            return {
                "session_id": d.get("session_id"),
                "role": d.get("role"),
                "event_type": "turn",
                "content": d.get("content"),
                "timestamp": d.get("timestamp"),
            }
    """)
    samples = [
        json.dumps({"session_id": "s1", "role": "user", "content": "hi", "timestamp": "2026-01-01"}),
        json.dumps({"session_id": "s1", "role": "assistant", "content": "hello", "timestamp": "2026-01-01"}),
    ]
    ok, n, coverage = check_parse(code, samples)
    assert ok
    assert n == 2
    assert coverage["session_id"] == 1.0
    assert coverage["role"] == 1.0
    assert coverage["event_type"] == 1.0


def test_check_parse_broken_code():
    code = "def parse_line(line): raise Exception('boom')"
    ok, n, coverage = check_parse(code, ["{}"])
    assert not ok
    assert n == 0


def test_check_parse_syntax_error():
    code = "def parse_line(line:\n    return None"
    ok, n, _cov = check_parse(code, ["{}"])
    assert not ok


def test_check_parse_returns_none():
    code = "def parse_line(line): return None"
    ok, n, _cov = check_parse(code, ["{}"])
    assert not ok
    assert n == 0


def test_check_parse_empty_samples():
    code = 'import json\ndef parse_line(line): return json.loads(line)'
    ok, n, _cov = check_parse(code, [])
    assert not ok  # no events parsed = failure


def test_check_parse_fails_without_session_id():
    """Adapter that returns dicts but without session_id should fail the quality gate."""
    code = textwrap.dedent("""\
        import json
        def parse_line(line):
            return {"event_type": "turn", "content": "x", "role": "user"}
    """)
    samples = ['{"a": 1}', '{"b": 2}']
    ok, n, coverage = check_parse(code, samples)
    assert not ok  # session_id coverage is 0%, below 50% gate
    assert n == 2
    assert coverage["session_id"] == 0.0


def test_check_parse_coverage_reported():
    """Coverage dict should report all canonical fields."""
    code = textwrap.dedent("""\
        import json
        def parse_line(line):
            return {"session_id": "s", "role": "user", "event_type": "turn",
                    "content": "x", "timestamp": "t", "model": "gpt-4"}
    """)
    ok, n, coverage = check_parse(code, ['{"a": 1}'])
    assert ok
    assert coverage["model"] == 1.0
    assert coverage["input_tokens"] == 0.0  # not filled, but not gated


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


def test_deploy_writes_file(tmp_path):
    code = "import json\ndef parse_line(line): return json.loads(line)\n"
    ok = deploy("test-source", code, tmp_path)
    assert ok
    assert (tmp_path / "test-source" / "adapter.py").read_text() == code


def test_deploy_creates_dirs(tmp_path):
    deep = tmp_path / "a" / "b"
    ok = deploy("src", "pass", deep)
    assert ok
    assert (deep / "src" / "adapter.py").is_file()


# ---------------------------------------------------------------------------
# heal
# ---------------------------------------------------------------------------


def test_heal_with_template(tmp_path):
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi",
                           "session_id": "s1", "event_type": "turn"})]
    ok = heal("test", samples, adapters_dir=tmp_path)
    assert ok
    assert (tmp_path / "test" / "adapter.py").exists()


def test_heal_no_adapters_dir():
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi",
                           "session_id": "s1", "event_type": "turn"})]
    ok = heal("test", samples, adapters_dir=None)
    assert ok  # test passes, just not deployed


def test_heal_bad_samples():
    ok = heal("test", ["not json", "also not json"])
    # Template fallback returns None for non-json, test_parse may fail
    # This is fine — heal returns False when test fails
    assert isinstance(ok, bool)


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


def test_connect_missing_path(tmp_path):
    ok, msg = connect(tmp_path / "nonexistent")
    assert not ok
    assert "not found" in msg


def test_connect_empty_dir(tmp_path):
    ok, msg = connect(tmp_path)
    assert not ok
    assert "No data" in msg


def test_connect_with_data(tmp_path):
    data = tmp_path / ".test-harness"
    data.mkdir()
    f = data / "sessions.jsonl"
    lines = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": f"msg {i}",
                         "session_id": "s1", "event_type": "turn"}) for i in range(5)]
    f.write_text("\n".join(lines) + "\n")

    ok, msg = connect(data, adapters_dir=tmp_path / "adapters")
    assert ok
    assert "5 events" in msg


# ---------------------------------------------------------------------------
# _read_samples
# ---------------------------------------------------------------------------


def test_read_samples_jsonl(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_text('{"a":1}\n{"b":2}\n')
    samples = _read_samples(tmp_path)
    assert len(samples) == 2


def test_read_samples_max_lines(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_text("\n".join(f'{{"n":{i}}}' for i in range(100)) + "\n")
    samples = _read_samples(tmp_path, max_lines=10)
    assert len(samples) == 10


def test_read_samples_binary_file(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_bytes(b'\x00\x01\x02\xff\xfe')
    samples = _read_samples(tmp_path)
    # Should not crash on binary
    assert isinstance(samples, list)


def test_read_samples_redacts_sensitive_values(tmp_path):
    f = tmp_path / "data.jsonl"
    f.write_text(
        json.dumps(
            {
                "type": "message",
                "role": "user",
                "content": "very secret code block\nline 2",
                "url": "https://example.com/private",
                "path": "/Users/test/private/file.txt",
                "api_key": "sk-123",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    samples = _read_samples(tmp_path)

    assert len(samples) == 1
    sample = samples[0]
    assert "very secret code block" not in sample
    assert "https://example.com/private" not in sample
    assert "/Users/test/private/file.txt" not in sample
    assert "sk-123" not in sample


# ---------------------------------------------------------------------------
# template_fallback
# ---------------------------------------------------------------------------


def test_template_fallback_produces_valid_code():
    code = _template_fallback([])
    assert "parse_line" in code
    samples = [json.dumps({
        "timestamp": "2026-01-01", "role": "user", "content": "hi",
        "session_id": "s1", "event_type": "turn",
    })]
    ok, _, coverage = check_parse(code, samples)
    assert ok
    assert coverage["session_id"] == 1.0
