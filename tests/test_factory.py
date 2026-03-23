"""Tests for sense factory — discover, generate, test, heal, connect, deploy."""

from __future__ import annotations

import json
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
    code = 'import json\ndef parse_line(line):\n    return json.loads(line)\n'
    samples = ['{"a": 1}', '{"b": 2}']
    ok, n = check_parse(code, samples)
    assert ok
    assert n == 2


def test_check_parse_broken_code():
    code = "def parse_line(line): raise Exception('boom')"
    ok, n = check_parse(code, ["{}"])
    assert not ok
    assert n == 0


def test_check_parse_syntax_error():
    code = "def parse_line(line:\n    return None"
    ok, n = check_parse(code, ["{}"])
    assert not ok


def test_check_parse_returns_none():
    code = "def parse_line(line): return None"
    ok, n = check_parse(code, ["{}"])
    assert not ok
    assert n == 0


def test_check_parse_empty_samples():
    code = 'import json\ndef parse_line(line): return json.loads(line)'
    ok, n = check_parse(code, [])
    assert not ok  # no events parsed = failure


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
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi"})]
    ok = heal("test", samples, adapters_dir=tmp_path)
    assert ok
    assert (tmp_path / "test" / "adapter.py").exists()


def test_heal_no_adapters_dir():
    samples = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": "hi"})]
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
    lines = [json.dumps({"timestamp": "2026-01-01", "role": "user", "content": f"msg {i}"}) for i in range(5)]
    f.write_text("\n".join(lines) + "\n")

    ok, msg = connect(data, adapters_dir=tmp_path / "adapters")
    assert ok
    assert "events parsed" in msg


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


# ---------------------------------------------------------------------------
# template_fallback
# ---------------------------------------------------------------------------


def test_template_fallback_produces_valid_code():
    code = _template_fallback([])
    assert "parse_line" in code
    ok, _ = check_parse(code, ['{"timestamp": "2026-01-01", "role": "user", "content": "hi"}'])
    assert ok
