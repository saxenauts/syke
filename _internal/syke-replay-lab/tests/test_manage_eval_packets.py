from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_manage_packets_module():
    module_path = Path(__file__).resolve().parents[1] / "manage_eval_packets.py"
    spec = importlib.util.spec_from_file_location("manage_eval_packets_for_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load manage_eval_packets module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_run_slug_uses_ablation_prefix() -> None:
    mod = _load_manage_packets_module()
    slug = mod.build_run_slug(ablation=3, label="Meta Postcheck", kind="eval", stamp="20260418T010203Z")
    assert slug == "ab03-meta-postcheck-eval-20260418T010203Z"


def test_upsert_single_run_packet(tmp_path: Path, monkeypatch) -> None:
    mod = _load_manage_packets_module()

    runs_root = tmp_path / "runs"
    monkeypatch.setattr(mod, "RUNS_ROOT", runs_root)
    manifest_path = runs_root / "eval_manifest.json"
    run_dir = runs_root / "ab03-meta-postcheck-eval-20260418T010203Z"
    run_dir.mkdir(parents=True)
    (run_dir / "benchmark_results.json").write_text("{}", encoding="utf-8")

    packet = mod.upsert_packet(
        manifest_path=manifest_path,
        name="ab03-meta-postcheck",
        description="single run",
        created_at="2026-04-18T01:02:03Z",
        run_dir=run_dir,
        conditions=[],
        require_pure=True,
    )

    assert packet["path"] == "./runs/ab03-meta-postcheck-eval-20260418T010203Z"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["packets"][0]["name"] == "ab03-meta-postcheck"


def test_upsert_composed_packet_requires_pure_by_default(tmp_path: Path, monkeypatch) -> None:
    mod = _load_manage_packets_module()

    runs_root = tmp_path / "runs"
    monkeypatch.setattr(mod, "RUNS_ROOT", runs_root)
    manifest_path = runs_root / "eval_manifest.json"

    prod_dir = runs_root / "ab03-prod-eval"
    prod_dir.mkdir(parents=True)
    (prod_dir / "benchmark_results.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="must include a pure condition"):
        mod.upsert_packet(
            manifest_path=manifest_path,
            name="ab03-no-pure",
            description=None,
            created_at="2026-04-18T01:02:03Z",
            run_dir=None,
            conditions=[{"name": "production", "source": "./runs/ab03-prod-eval"}],
            require_pure=True,
        )


def test_parse_condition_entry_supports_source_condition_override(tmp_path: Path, monkeypatch) -> None:
    mod = _load_manage_packets_module()

    runs_root = tmp_path / "runs"
    monkeypatch.setattr(mod, "RUNS_ROOT", runs_root)

    run_dir = runs_root / "ab07-eval"
    run_dir.mkdir(parents=True)
    (run_dir / "benchmark_results.json").write_text("{}", encoding="utf-8")

    parsed = mod._parse_condition_entry(f"syke_meta={run_dir}@production")

    assert parsed["name"] == "syke_meta"
    assert parsed["source"] == "./runs/ab07-eval"
    assert parsed["source_condition"] == "production"
