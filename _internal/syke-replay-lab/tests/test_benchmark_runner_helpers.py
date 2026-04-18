from __future__ import annotations

import importlib.util
import json
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
    assert benchmark_runner._ask_mode_for_condition("production") == "syke"
    assert benchmark_runner._ask_mode_for_condition("syke_meta_postcheck") == "syke"
    assert benchmark_runner._ask_mode_for_condition("custom:guard.md") == "syke"

    with pytest.raises(ValueError, match="Condition name cannot be empty"):
        benchmark_runner._ask_mode_for_condition("   ")


def test_build_ask_prompt_respects_condition_semantics(monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    fake_prompt_module = type(
        "M",
        (),
        {
            "_build_psyche_md": staticmethod(
                lambda workspace_root, home=None: "<psyche>IDENTITY</psyche>"
            ),
            "_build_memex_block": staticmethod(
                lambda db, user_id, context="ask": "\n\n<memex>MAP</memex>"
            ),
            "build_prompt": staticmethod(
                lambda workspace_root, db=None, user_id=None, home=None, synthesis_path=None: (
                    "<psyche>IDENTITY</psyche>\n\n"
                    "<memex>MAP</memex>\n\n"
                    f"<synthesis>{getattr(synthesis_path, 'name', 'CTRL')}</synthesis>"
                )
            ),
        },
    )
    monkeypatch.setitem(sys.modules, "syke.runtime.psyche_md", fake_prompt_module)

    pure_prompt = benchmark_runner._build_ask_prompt(
        workspace_root=Path("/tmp/workspace"),
        db=object(),
        user_id="user",
        question="Where was I?",
        ask_mode="pure",
    )
    zero_prompt = benchmark_runner._build_ask_prompt(
        workspace_root=Path("/tmp/workspace"),
        db=object(),
        user_id="user",
        question="Where was I?",
        ask_mode="zero",
    )
    syke_prompt = benchmark_runner._build_ask_prompt(
        workspace_root=Path("/tmp/workspace"),
        db=object(),
        user_id="user",
        question="Where was I?",
        ask_mode="syke",
    )

    assert "<psyche>IDENTITY</psyche>" in pure_prompt
    assert "<memex>MAP</memex>" not in pure_prompt
    assert "<synthesis>CTRL</synthesis>" not in pure_prompt

    assert "<psyche>IDENTITY</psyche>" in zero_prompt
    assert "<memex>MAP</memex>" in zero_prompt
    assert "<synthesis>CTRL</synthesis>" not in zero_prompt

    assert "<psyche>IDENTITY</psyche>" in syke_prompt
    assert "<memex>MAP</memex>" in syke_prompt
    assert "<synthesis>CTRL</synthesis>" in syke_prompt
    assert "User question: Where was I?" in syke_prompt


def test_build_ask_prompt_passes_synthesis_override(monkeypatch) -> None:
    benchmark_runner = _load_benchmark_runner_module()

    fake_prompt_module = type(
        "M",
        (),
        {
            "_build_psyche_md": staticmethod(
                lambda workspace_root, home=None: "<psyche>IDENTITY</psyche>"
            ),
            "_build_memex_block": staticmethod(
                lambda db, user_id, context="ask": "\n\n<memex>MAP</memex>"
            ),
            "build_prompt": staticmethod(
                lambda workspace_root, db=None, user_id=None, home=None, synthesis_path=None: (
                    f"<synthesis>{synthesis_path.name if synthesis_path else 'default'}</synthesis>"
                )
            ),
        },
    )
    monkeypatch.setitem(sys.modules, "syke.runtime.psyche_md", fake_prompt_module)

    syke_prompt = benchmark_runner._build_ask_prompt(
        workspace_root=Path("/tmp/workspace"),
        db=object(),
        user_id="user",
        question="Where was I?",
        ask_mode="syke",
        synthesis_path=Path("/tmp/guard.md"),
    )

    assert "<synthesis>guard.md</synthesis>" in syke_prompt


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
                "condition": "production",
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
                "condition": "production",
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
    assert summary["production"]["counts"]["pass"] == 1
    assert summary["production"]["counts"]["partial"] == 1
    assert summary["production"]["success_rate"] == 0.5
    assert summary["production"]["zero_search_success_rate"] == 0.5
    assert summary["production"]["tool_calls_per_success"] == 1.0
    assert summary["production"]["cost_per_success"] == 0.07
    assert summary["production"]["duration_ms_per_success"] == 5000.0
    assert summary["production"]["tokens_per_success"] == 75.0
    assert summary["production"]["total_input_tokens"] == 50
    assert summary["production"]["total_output_tokens"] == 25


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
        condition="production",
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
                    "condition": "production",
                    "skill_content": "default production synthesis block",
                },
                "timeline": [{"cycle": 1, "cycle_cutoff_iso": "2026-03-07T23:59:00-08:00"}],
            }
        ),
        encoding="utf-8",
    )

    spec = benchmark_runner._load_replay_condition_spec(
        condition="production",
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
    item = {"probe_id": "P10", "reference_dt": "2026-03-14"}

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

    workspace_root, slice_dir = benchmark_runner._build_probe_workspace(
        bundle_path=Path("/tmp/bundle"),
        item=item,
        memex_content="",
        output_dir=Path("/tmp/run-output"),
        condition="pure",
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
        condition="production",
        ask_mode="syke",
        memex_chars=123,
    )

    assert packet["raw_context"]["slice_summary"]["sources"]["claude-code"]["jsonl_files"] == 2
    assert packet["raw_context"]["replay_state"]["condition"] == "production"
    assert packet["local_git_set"]["available"] is True
    assert "useful_means" in packet["judge_brief"]
