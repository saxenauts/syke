"""Tests for AdapterSandbox."""

from syke.sense.sandbox import AdapterSandbox


def test_sandbox_rejects_bad_code():
    sandbox = AdapterSandbox()
    bad_code = """
import socket
def parse_line(line):
    eval(line)
    return None
"""
    result = sandbox.test_adapter(bad_code, ['{"test": 1}'])
    assert not result.success
    assert any("Forbidden" in e for e in result.errors)


def test_sandbox_accepts_good_code():
    sandbox = AdapterSandbox()
    good_code = """
import json
def parse_line(line):
    data = json.loads(line)
    return data
"""
    result = sandbox.test_adapter(good_code, ['{"key": "value"}', '{"key2": "value2"}'])
    assert result.success
    assert result.events_parsed == 2


def test_sandbox_enforces_timeout():
    sandbox = AdapterSandbox(timeout=1)
    slow_code = """
import time
def parse_line(line):
    time.sleep(10)
    return None
"""
    result = sandbox.test_adapter(slow_code, ['{"test": 1}'])
    assert not result.success
    assert any("Timeout" in e for e in result.errors)
