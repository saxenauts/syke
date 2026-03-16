"""Tests for LLM adapter generator."""

import json
from syke.sense.adapter_generator import AdapterGenerator
from syke.sense.analyzer import AnalysisResult


def _make_analysis():
    return AnalysisResult(
        format="jsonl",
        timestamp_field="timestamp",
        role_field="role",
        content_field="content",
        tool_fields=[],
        confidence=0.8,
    )


def test_generator_produces_valid_python():
    gen = AdapterGenerator()
    result = gen.generate(
        _make_analysis(),
        [
            json.dumps(
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "role": "user",
                    "content": "hello",
                }
            )
        ],
        source_name="test-harness",
    )
    assert result.adapter_code
    assert "def parse_line" in result.adapter_code
    compile(result.adapter_code, "<test>", "exec")  # valid Python


def test_generator_produces_descriptor():
    gen = AdapterGenerator()
    result = gen.generate(
        _make_analysis(),
        [
            json.dumps(
                {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "role": "user",
                    "content": "hi",
                }
            )
        ],
    )
    assert "[harness]" in result.descriptor_toml
    assert "format" in result.descriptor_toml


def test_generator_retries_on_sandbox_failure():
    attempts = []

    def bad_llm(prompt):
        attempts.append(1)
        if len(attempts) < 3:
            return "import socket\ndef parse_line(line): return None"
        return "import json\ndef parse_line(line): return json.loads(line)"

    gen = AdapterGenerator(llm_fn=bad_llm, max_retries=3)
    result = gen.generate(
        _make_analysis(),
        [json.dumps({"key": "value"})],
    )
    assert len(attempts) >= 2  # Retried at least once
