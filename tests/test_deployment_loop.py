"""Tests for adapter persistence: deploy, list, connect+deploy, heal+deploy."""

from __future__ import annotations

from pathlib import Path

from syke.sense.adapter_generator import GeneratedAdapter
from syke.sense.intelligence import SenseIntelligence
from syke.sense.sandbox import SandboxResult


def _make_generated(success: bool = True) -> GeneratedAdapter:
    code = "import json\ndef parse_line(line):\n    return json.loads(line)\n"
    descriptor = '[harness]\nname = "test"\nformat = "jsonl"\n'
    test_code = "def test_noop(): pass\n"
    result = SandboxResult(success=success, events_parsed=3 if success else 0)
    return GeneratedAdapter(
        descriptor_toml=descriptor,
        adapter_code=code,
        test_code=test_code,
        sandbox_result=result,
    )


def test_deploy_writes_files(tmp_path):
    si = SenseIntelligence(adapters_dir=tmp_path / "adapters")
    gen = _make_generated()
    ok = si.deploy("test-harness", gen)
    assert ok
    target = tmp_path / "adapters" / "test-harness"
    assert (target / "adapter.py").read_text().startswith("import json")
    assert (target / "descriptor.toml").read_text().startswith("[harness]")
    assert (target / "test_adapter.py").exists()


def test_deploy_creates_dirs(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    si = SenseIntelligence(adapters_dir=deep)
    ok = si.deploy("src", _make_generated())
    assert ok
    assert (deep / "src" / "adapter.py").is_file()


def test_deploy_no_adapters_dir():
    si = SenseIntelligence(adapters_dir=None)
    ok = si.deploy("x", _make_generated())
    assert not ok


def test_list_deployed(tmp_path):
    adapters = tmp_path / "adapters"
    (adapters / "alpha").mkdir(parents=True)
    (adapters / "alpha" / "adapter.py").write_text("pass")
    (adapters / "beta").mkdir(parents=True)
    (adapters / "beta" / "adapter.py").write_text("pass")
    (adapters / "empty").mkdir(parents=True)

    result = SenseIntelligence.list_deployed(adapters)
    assert result == ["alpha", "beta"]


def test_list_deployed_empty(tmp_path):
    assert SenseIntelligence.list_deployed(tmp_path / "nonexistent") == []


def test_deploy_overwrites(tmp_path):
    si = SenseIntelligence(adapters_dir=tmp_path)
    si.deploy("src", _make_generated())
    old = (tmp_path / "src" / "adapter.py").read_text()

    gen2 = _make_generated()
    gen2.adapter_code = "# v2\n" + gen2.adapter_code
    si.deploy("src", gen2)
    new = (tmp_path / "src" / "adapter.py").read_text()
    assert new != old
    assert new.startswith("# v2")
