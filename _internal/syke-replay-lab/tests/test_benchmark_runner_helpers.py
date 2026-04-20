from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest


def _load_benchmark_runner_module():
    module_path = Path(__file__).resolve().parents[1] / "benchmark_runner.py"
    spec = importlib.util.spec_from_file_location("benchmark_runner_for_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load benchmark_runner module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ask_mode_for_condition_enforces_current_split() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    assert benchmark_runner._ask_mode_for_condition("pure") == "pure"
    assert benchmark_runner._ask_mode_for_condition("zero") == "zero"
    assert benchmark_runner._canonical_condition_name("production") == "syke"
    assert benchmark_runner._ask_mode_for_condition("production") == "syke"
    assert benchmark_runner._ask_mode_for_condition("syke_meta_postcheck") == "syke"
    assert benchmark_runner._ask_mode_for_condition("custom:guard.md") == "syke"

    with pytest.raises(ValueError, match="Condition name cannot be empty"):
        benchmark_runner._ask_mode_for_condition("   ")


def test_build_ask_prompt_respects_condition_semantics(monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    def fake_build_prompt(
        workspace_root,
        db=None,
        user_id=None,
        *,
        now,
        home=None,
        context="ask",
        synthesis_path=None,
        last_synthesis=None,
        cycle=None,
        include_memex=True,
        include_synthesis=True,
        time_directive=True,
    ):
        parts = ["<psyche>IDENTITY</psyche>", f"<now>{now}</now>"]
        if include_memex and db is not None and user_id is not None:
            parts.append("<memex>MAP</memex>")
        if include_synthesis:
            name = getattr(synthesis_path, "name", "CTRL")
            parts.append(f"<synthesis>{name}</synthesis>")
        return "\n\n".join(parts)

    fake_prompt_module = type(
        "M",
        (),
        {"build_prompt": staticmethod(fake_build_prompt)},
    )
    monkeypatch.setitem(sys.modules, "syke.runtime.psyche_md", fake_prompt_module)

    common_kwargs = {
        "workspace_root": Path("/tmp/workspace"),
        "db": object(),
        "user_id": "user",
        "question": "Where was I?",
        "reference_ts_local": "2026-03-07 18:02 PST",
        "reference_cutoff_iso": "2026-03-07T18:02:00-08:00",
    }

    pure_prompt = benchmark_runner._build_ask_prompt(ask_mode="pure", **common_kwargs)
    zero_prompt = benchmark_runner._build_ask_prompt(ask_mode="zero", **common_kwargs)
    syke_prompt = benchmark_runner._build_ask_prompt(ask_mode="syke", **common_kwargs)

    # All three carry <psyche> and the <now> block with the probe cutoff.
    for prompt in (pure_prompt, zero_prompt, syke_prompt):
        assert "<psyche>IDENTITY</psyche>" in prompt
        assert "<now>" in prompt
        assert "2026-03-07 18:02 PST (cutoff 2026-03-07T18:02:00-08:00)" in prompt
        assert "User question: Where was I?" in prompt

    # pure = identity + time only
    assert "<memex>MAP</memex>" not in pure_prompt
    assert "<synthesis>" not in pure_prompt

    # zero adds memex, no synthesis
    assert "<memex>MAP</memex>" in zero_prompt
    assert "<synthesis>" not in zero_prompt

    # syke has the full stack
    assert "<memex>MAP</memex>" in syke_prompt
    assert "<synthesis>CTRL</synthesis>" in syke_prompt

    # No separate <reference_time> block anywhere — unified into <now>
    for prompt in (pure_prompt, zero_prompt, syke_prompt):
        assert "<reference_time>" not in prompt


def test_build_ask_prompt_passes_synthesis_override(monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    def fake_build_prompt(
        workspace_root,
        db=None,
        user_id=None,
        *,
        now,
        home=None,
        context="ask",
        synthesis_path=None,
        last_synthesis=None,
        cycle=None,
        include_memex=True,
        include_synthesis=True,
        time_directive=True,
    ):
        name = synthesis_path.name if synthesis_path else "default"
        return f"<synthesis>{name}</synthesis>"

    fake_prompt_module = type(
        "M",
        (),
        {"build_prompt": staticmethod(fake_build_prompt)},
    )
    monkeypatch.setitem(sys.modules, "syke.runtime.psyche_md", fake_prompt_module)

    syke_prompt = benchmark_runner._build_ask_prompt(
        workspace_root=Path("/tmp/workspace"),
        db=object(),
        user_id="user",
        question="Where was I?",
        ask_mode="syke",
        reference_ts_local="2026-03-07 18:02 PST",
        reference_cutoff_iso="2026-03-07T18:02:00-08:00",
        synthesis_path=Path("/tmp/guard.md"),
    )

    assert "<synthesis>guard.md</synthesis>" in syke_prompt


def test_reference_now_string_combines_local_and_iso_anchors() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    line = benchmark_runner._reference_now_string(
        reference_ts_local="2026-03-07 18:02 PST",
        reference_cutoff_iso="2026-03-07T18:02:00-08:00",
    )

    assert line == "2026-03-07 18:02 PST (cutoff 2026-03-07T18:02:00-08:00)"


def test_write_reference_time_file_emits_directive(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    benchmark_runner._write_reference_time_file(
        tmp_path,
        reference_ts_local="2026-03-07 18:02 PST",
        reference_cutoff_iso="2026-03-07T18:02:00-08:00",
    )

    content = (tmp_path / "REFERENCE_TIME.md").read_text(encoding="utf-8")
    assert "As-of local time: 2026-03-07 18:02 PST" in content
    assert "As-of cutoff ISO: 2026-03-07T18:02:00-08:00" in content
    assert "Do not use wall-clock time" in content


def test_prepare_frozen_time_tools_overrides_date_command(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    bin_dir = benchmark_runner._prepare_frozen_time_tools(
        tmp_path,
        reference_dt=datetime.fromisoformat("2026-03-07T18:02:00-08:00"),
    )

    import subprocess

    env = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    local = subprocess.check_output(["date", "+%Y-%m-%d %H:%M %Z"], text=True, env=env).strip()
    utc = subprocess.check_output(["date", "-u", "+%Y-%m-%d %H:%M %Z"], text=True, env=env).strip()

    assert local.startswith("2026-03-07 18:02")
    assert utc.startswith("2026-03-08 02:02")


def test_inject_memex_uses_reference_timestamp_override(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    db_path = tmp_path / "syke.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE memories (id TEXT, user_id TEXT, content TEXT, source_event_ids TEXT, created_at TEXT, updated_at TEXT, active INTEGER)"
    )
    conn.commit()
    conn.close()

    benchmark_runner._inject_memex(
        db_path,
        "timefixed memex",
        timestamp_override="2026-03-07T18:02:00-08:00",
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT created_at, updated_at FROM memories WHERE content = ?",
        ("timefixed memex",),
    ).fetchone()
    conn.close()

    assert row == ("2026-03-07T18:02:00-08:00", "2026-03-07T18:02:00-08:00")


def test_temporary_env_sets_and_restores_environment(monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    monkeypatch.setenv("SYKE_PROVIDER", "baseline")

    with benchmark_runner._temporary_env({"SYKE_PROVIDER": "override", "SYKE_EXTRA": "1"}):
        assert benchmark_runner.os.environ["SYKE_PROVIDER"] == "override"
        assert benchmark_runner.os.environ["SYKE_EXTRA"] == "1"

    assert benchmark_runner.os.environ["SYKE_PROVIDER"] == "baseline"
    assert "SYKE_EXTRA" not in benchmark_runner.os.environ


def test_infer_judge_provider_from_source_rows_uses_existing_metadata(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    evidence_dir = tmp_path / "evidence" / "pure" / "R01"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "judge_metadata.json").write_text(
        json.dumps({"provider": "azure-anthropic-foundry", "model": "claude-opus-4-6"}),
        encoding="utf-8",
    )

    provider = benchmark_runner._infer_judge_provider_from_source_rows(
        [{"artifacts": {"evidence_dir": str(evidence_dir)}}],
        judge_model="claude-opus-4-6",
    )

    assert provider == "azure-anthropic-foundry"


def test_resolve_judge_provider_for_rerun_prefers_source_artifacts_over_fallback(
    tmp_path: Path,
) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(
        json.dumps({"judge_model": "claude-opus-4-6", "judge_provider": None}),
        encoding="utf-8",
    )
    evidence_dir = run_dir / "evidence" / "pure" / "R01"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "judge_metadata.json").write_text(
        json.dumps({"provider": "azure-anthropic-foundry", "model": "claude-opus-4-6"}),
        encoding="utf-8",
    )

    provider = benchmark_runner._resolve_judge_provider_for_rerun(
        source_run=run_dir,
        source_rows=[{"artifacts": {"evidence_dir": str(evidence_dir)}}],
        requested_judge_model="claude-opus-4-6",
        fallback_provider="openai-codex",
    )

    assert provider == "azure-anthropic-foundry"


def test_shared_probe_paths_are_condition_independent(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    item = {"probe_id": "R01", "dataset_id": "NE-1.3"}
    out = tmp_path / "run-out"

    slice_dir = benchmark_runner._shared_slice_dir(out, item)
    git_anchor = benchmark_runner._shared_git_anchor_path(out, item)

    assert slice_dir.name == "NE-1.3__R01"
    assert git_anchor.name == "NE-1.3__R01.json"
    assert "slices" in str(slice_dir)
    assert "git_anchors" in str(git_anchor)


def test_ensure_probe_git_anchor_reuses_existing_file(tmp_path: Path, monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    item = {"probe_id": "R01", "dataset_id": "NE-1.3"}
    out = tmp_path / "run-out"
    existing = benchmark_runner._shared_git_anchor_path(out, item)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text('{"ok": true}', encoding="utf-8")

    called = {"count": 0}

    def _fake_build_local_git_anchor(item, output_path):
        called["count"] += 1
        return output_path

    monkeypatch.setattr(benchmark_runner, "_build_local_git_anchor", _fake_build_local_git_anchor)

    got = benchmark_runner._ensure_probe_git_anchor(item=item, output_dir=out)

    assert got == existing
    assert called["count"] == 0


def test_summarize_results_tracks_useful_and_efficiency() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    summary = benchmark_runner._summarize_results(
        [
            {
                "condition": "pure",
                "verdict": "fail",
                "tool_calls": 5,
                "cost_usd": 0.08,
                "zero_search": False,
                "answer_metadata": {
                    "duration_ms": 1000,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_read_tokens": 0,
                },
            },
            {
                "condition": "syke",
                "verdict": "partial",
                "tool_calls": 1,
                "cost_usd": 0.04,
                "zero_search": False,
                "answer_metadata": {
                    "duration_ms": 2000,
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "cache_read_tokens": 3,
                },
            },
            {
                "condition": "syke",
                "verdict": "pass",
                "tool_calls": 0,
                "cost_usd": 0.03,
                "zero_search": True,
                "answer_metadata": {
                    "duration_ms": 3000,
                    "input_tokens": 30,
                    "output_tokens": 15,
                    "cache_read_tokens": 5,
                },
            },
        ]
    )

    assert summary["pure"]["success_rate"] == 0.0
    assert summary["pure"]["avg_tool_calls"] == 5
    assert summary["syke"]["counts"]["pass"] == 1
    assert summary["syke"]["counts"]["partial"] == 1
    assert summary["syke"]["success_rate"] == 0.5
    assert summary["syke"]["zero_search_success_rate"] == 0.5
    assert summary["syke"]["tool_calls_per_success"] == 1.0
    assert summary["syke"]["cost_per_success"] == 0.07
    assert summary["syke"]["duration_ms_per_success"] == 5000.0
    assert summary["syke"]["tokens_per_success"] == 75.0
    assert summary["syke"]["total_input_tokens"] == 50
    assert summary["syke"]["total_output_tokens"] == 25


def test_find_cycle_matched_memex_prefers_exact_cutoff() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    timeline = [
        {
            "source_day": "2026-03-07",
            "cycle_cutoff_iso": "2026-03-07T12:00:00-08:00",
            "memex_content": "midday",
        },
        {
            "source_day": "2026-03-07",
            "cycle_cutoff_iso": "2026-03-07T23:59:00-08:00",
            "memex_content": "end_of_day",
        },
    ]

    matched = benchmark_runner._find_cycle_matched_memex(
        timeline,
        datetime.fromisoformat("2026-03-07T18:02:00-08:00"),
    )

    assert matched == "midday"


def test_find_cycle_matched_memex_rejects_legacy_day_match() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    timeline = [
        {"source_day": "2026-03-07", "memex_content": "legacy_day"},
        {"source_day": "2026-03-08", "memex_content": "next_day"},
    ]

    with pytest.raises(ValueError, match="Replay results must be timefixed"):
        benchmark_runner._find_cycle_matched_memex(
            timeline,
            datetime.fromisoformat("2026-03-07T18:02:00-08:00"),
        )


def test_validate_timefixed_timeline_requires_cycle_cutoffs() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    with pytest.raises(ValueError, match="Replay results must be timefixed"):
        benchmark_runner._validate_timefixed_timeline(
            [{"cycle": 1, "memex_content": "x"}],
            replay_path=Path("/tmp/replay_results.json"),
        )


def test_load_replay_condition_spec_requires_matching_replay_condition(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    (replay_dir / "replay_results.json").write_text(
        json.dumps(
            {
                "metadata": {"condition": "zero"},
                "timeline": [{"cycle": 1, "cycle_cutoff_iso": "2026-03-07T23:59:00-08:00"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match replay source condition"):
        benchmark_runner._load_replay_condition_spec(
        condition="syke",
        replay_dir=replay_dir,
        synthesis_path=None,
    )


def test_load_replay_condition_spec_accepts_matching_timefixed_replay(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    (replay_dir / "replay_results.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "condition": "syke",
                    "skill_content": "default syke synthesis block",
                },
                "timeline": [{"cycle": 1, "cycle_cutoff_iso": "2026-03-07T23:59:00-08:00"}],
            }
        ),
        encoding="utf-8",
    )

    spec = benchmark_runner._load_replay_condition_spec(
        condition="syke",
        replay_dir=replay_dir,
        synthesis_path=None,
    )

    assert spec["ask_mode"] == "syke"
    assert spec["replay_source"] == str(replay_dir.resolve())
    assert len(spec["timeline"]) == 1


def test_load_replay_condition_spec_accepts_custom_syke_condition(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    (replay_dir / "replay_results.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "condition": "syke_meta_postcheck",
                    "skill_content": "custom synthesis block",
                },
                "timeline": [{"cycle": 1, "cycle_cutoff_iso": "2026-03-07T23:59:00-08:00"}],
            }
        ),
        encoding="utf-8",
    )

    spec = benchmark_runner._load_replay_condition_spec(
        condition="syke_meta_postcheck",
        replay_dir=replay_dir,
        synthesis_path=None,
    )

    assert spec["ask_mode"] == "syke"
    assert spec["replay_condition"] == "syke_meta_postcheck"
    assert spec["skill_content"] == "custom synthesis block"


def test_build_probe_workspace_uses_syke_lab_root(tmp_path: Path, monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    fake_home = tmp_path / "fake-home"
    monkeypatch.setattr(benchmark_runner.Path, "home", lambda: fake_home)
    item = {"probe_id": "P10", "reference_dt": "2026-03-14", "dataset_id": "NE-1.3"}

    captured: dict[str, Path] = {}

    def fake_slice_bundle(bundle_path, reference_dt, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "adapters").mkdir(parents=True, exist_ok=True)
        captured["slice_dir"] = output_dir

    def fake_configure_bundle_workspace(probe_dir, bundle_path):
        workspace = probe_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        db_path = workspace / "syke.db"
        db_path.touch()
        captured["probe_dir"] = probe_dir
        return workspace, db_path

    def fake_rewire_adapters_to_slice(workspace_root, slice_dir):
        captured["rewire_slice_dir"] = slice_dir

    class _FakeDB:
        def __init__(self, path):
            self.path = path

        def close(self):
            return None

    monkeypatch.setitem(
        __import__("sys").modules,
        "cycle_slicer",
        type("M", (), {"slice_bundle": fake_slice_bundle}),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "memory_replay",
        type(
            "M",
            (),
            {
                "configure_bundle_workspace": fake_configure_bundle_workspace,
                "rewire_adapters_to_slice": fake_rewire_adapters_to_slice,
            },
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "syke.db", type("M", (), {"SykeDB": _FakeDB}))
    monkeypatch.setattr(benchmark_runner, "_inject_memex", lambda *_args, **_kwargs: None)

    shared_slice_dir = benchmark_runner._ensure_probe_slice(
        bundle_path=Path("/tmp/bundle"),
        item=item,
        output_dir=Path("/tmp/run-output"),
    )

    workspace_root, slice_dir = benchmark_runner._build_probe_workspace(
        bundle_path=Path("/tmp/bundle"),
        item=item,
        memex_content="",
        output_dir=Path("/tmp/run-output"),
        condition="pure",
        slice_dir=shared_slice_dir,
    )

    expected_root = fake_home / ".syke-lab" / "run-output"
    assert str(slice_dir).startswith(str(expected_root / "slices"))
    # workspace is at ~/.syke-lab/<run>/<condition>__<probe_id>/workspace
    assert str(workspace_root).startswith(str(expected_root))
    assert "pure__P10" in str(workspace_root)


def test_build_judge_prompt_is_neutral() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    prompt = benchmark_runner._build_judge_prompt(
        packet_path=Path("/tmp/packet.json"),
        slice_path=Path("/tmp/slice"),
        local_git_anchor=Path("/tmp/local_git_anchor.json"),
    )

    assert "You are a judge verifying a memory system's answer" in prompt
    assert "Treat the packet and the raw slice as authoritative." in prompt
    assert "Do not import any separate memory-maintenance" in prompt
    assert "Score three judge axes" in prompt
    assert "coherence" in prompt
    assert "The MEMEX below is your current map." not in prompt
    assert "Continuity is the default." not in prompt


def test_judge_schema_exposes_three_axes_with_subcategories() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    schema = benchmark_runner.JUDGE_SCHEMA
    assert "coherence" in schema["required"]
    assert "coherence" in schema["properties"]
    continuity = schema["properties"]["continuity"]
    coherence = schema["properties"]["coherence"]
    assert "subcategories" in continuity["properties"]
    assert "active_thread_selection" in continuity["properties"]["subcategories"]["properties"]
    assert "contradiction_handling" in coherence["properties"]["subcategories"]["properties"]


def test_extract_judge_json_rejects_legacy_under_specified_payload() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    payload = {
        "factual_grounding": {"score": "strong", "reasoning": "ok"},
        "continuity": {"score": "strong", "reasoning": "ok"},
        "overall_verdict": "pass",
        "summary": "legacy payload missing coherence and subcategories",
    }

    assert benchmark_runner._extract_judge_json(json.dumps(payload)) is None


def test_extract_judge_json_accepts_full_structured_payload() -> None:
    benchmark_runner = _load_benchmark_runner_module()

    def _dim(names: list[str]) -> dict[str, object]:
        return {
            "score": "strong",
            "reasoning": "grounded",
            "subcategories": {
                name: {"score": "strong", "reasoning": f"{name} grounded"} for name in names
            },
        }

    payload = {
        "factual_grounding": _dim(
            ["support", "boundedness", "uncertainty_calibration"]
        ),
        "continuity": _dim(
            [
                "active_thread_selection",
                "salience_relevance",
                "state_transition_tracking",
                "forgetting_residue_control",
                "continuation_value",
            ]
        ),
        "coherence": _dim(
            [
                "cross_harness_braid",
                "cross_session_consistency",
                "artifact_routing_consistency",
                "contradiction_handling",
            ]
        ),
        "overall_verdict": "pass",
        "summary": "valid full payload",
    }

    assert benchmark_runner._extract_judge_json(json.dumps(payload)) == payload


def test_build_real_ask_packet_includes_rich_context(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    slice_dir = tmp_path / "slice"
    slice_dir.mkdir()
    (slice_dir / "slice_meta.json").write_text(
        json.dumps(
            {
                "cycle": "2026-03-07T18:02:00-08:00",
                "bundle": "ne-1.3",
                "sources": {"claude-code": {"jsonl_files": 2, "jsonl_lines": 10}},
                "total_elapsed_sec": 0.5,
            }
        ),
        encoding="utf-8",
    )
    anchor = tmp_path / "local_git_anchor.json"
    anchor.write_text("{}", encoding="utf-8")

    item = {
        "probe_id": "R01",
        "prompt_text": "what happened last?",
        "family": "real-ask",
        "reference_dt": "2026-03-08",
        "reference_ts_local": "2026-03-07 18:02 PST",
        "source_surface": "claude-code",
        "source_ref": "x.jsonl#L4",
    }

    packet = benchmark_runner._build_real_ask_packet(
        item=item,
        answer_text="answer",
        answer_metadata={"tool_calls": 2},
        slice_dir=slice_dir,
        local_git_anchor=anchor,
        condition="syke",
        ask_mode="syke",
        memex_chars=123,
    )

    assert packet["raw_context"]["slice_summary"]["sources"]["claude-code"]["jsonl_files"] == 2
    assert packet["raw_context"]["replay_state"]["condition"] == "syke"
    assert packet["local_git_set"]["available"] is True
    assert "useful_means" in packet["judge_brief"]


def test_load_existing_results_reads_benchmark_results_items(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "benchmark_results.json").write_text(
        json.dumps({"items": [{"probe_id": "R01", "condition": "pure"}]}),
        encoding="utf-8",
    )

    rows = benchmark_runner._load_existing_results(run_dir)
    assert len(rows) == 1
    assert rows[0]["probe_id"] == "R01"


def test_load_existing_results_falls_back_to_results_json(tmp_path: Path) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(
        json.dumps([{"probe_id": "R01", "condition": "pure"}]),
        encoding="utf-8",
    )

    rows = benchmark_runner._load_existing_results(run_dir)
    assert len(rows) == 1
    assert rows[0]["condition"] == "pure"
