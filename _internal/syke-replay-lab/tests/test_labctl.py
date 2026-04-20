from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_labctl_module():
    module_path = Path(__file__).resolve().parents[1] / "labctl.py"
    spec = importlib.util.spec_from_file_location("labctl_for_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load labctl module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_progress_replay_reads_checkpoint(tmp_path: Path) -> None:
    labctl = _load_labctl_module()

    run = labctl.ManagedRun(
        run_id="replay-test",
        phase="replay",
        label="replay",
        status="running",
        created_at="2026-04-18T00:00:00+00:00",
        owner_cmd=["python", "memory_replay.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "replay-out"),
    )
    output_dir = Path(run.output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "replay_results.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "status": "running",
                    "partial": True,
                    "started_at": "2026-04-18T00:00:00+00:00",
                    "completed_cycles": 3,
                    "selected_replay_cycles": 10,
                    "last_completed_day": "2026-03-12",
                }
            }
        ),
        encoding="utf-8",
    )

    progress = labctl._extract_progress(run)

    assert progress.completed_units == 3
    assert progress.total_units == 10
    assert progress.unit_label == "cycles"
    assert progress.last_successful_unit == "2026-03-12"


def test_extract_progress_benchmark_reads_config_and_results(tmp_path: Path) -> None:
    labctl = _load_labctl_module()

    run = labctl.ManagedRun(
        run_id="bench-test",
        phase="benchmark",
        label="bench",
        status="running",
        created_at="2026-04-18T00:00:00+00:00",
        owner_cmd=["python", "benchmark_runner.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "bench-out"),
    )
    output_dir = Path(run.output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "started_at": "2026-04-18T00:00:00+00:00",
                "probes": ["R01", "R02"],
                "conditions": [{"name": "pure"}, {"name": "syke"}],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "results.json").write_text(
        json.dumps(
            [
                {"probe_id": "R01", "condition": "pure"},
                {"probe_id": "R01", "condition": "syke"},
            ]
        ),
        encoding="utf-8",
    )

    progress = labctl._extract_progress(run)

    assert progress.completed_units == 2
    assert progress.total_units == 4
    assert progress.unit_label == "rollouts"


def test_extract_progress_prefers_run_status_json(tmp_path: Path) -> None:
    labctl = _load_labctl_module()

    run = labctl.ManagedRun(
        run_id="bench-live",
        phase="benchmark",
        label="bench-live",
        status="running",
        created_at="2026-04-20T00:00:00+00:00",
        owner_cmd=["python", "benchmark_runner.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "bench-live"),
    )
    output_dir = Path(run.output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / "run_status.json").write_text(
        json.dumps(
            {
                "completed_units": 5,
                "total_units": 57,
                "unit_label": "rollouts",
                "message": "asks 9/57, judges 5/57",
            }
        ),
        encoding="utf-8",
    )

    progress = labctl._extract_progress(run)

    assert progress.completed_units == 5
    assert progress.total_units == 57
    assert progress.unit_label == "rollouts"
    assert progress.message == "asks 9/57, judges 5/57"


def test_runner_python_prefers_repo_venv(monkeypatch, tmp_path: Path) -> None:
    labctl = _load_labctl_module()

    fake_repo = tmp_path / "repo"
    fake_python = fake_repo / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(labctl, "REPO_ROOT", fake_repo)
    monkeypatch.delenv("SYKE_LAB_PYTHON", raising=False)

    assert labctl._runner_python() == str(fake_python)


def test_runner_python_respects_override(monkeypatch, tmp_path: Path) -> None:
    labctl = _load_labctl_module()

    override = tmp_path / "custom-python"
    override.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("SYKE_LAB_PYTHON", str(override))

    assert labctl._runner_python() == str(override)


def test_submit_benchmark_infers_dependencies_from_replay_paths(
    tmp_path: Path, monkeypatch
) -> None:
    labctl = _load_labctl_module()

    replay_out = tmp_path / "replay-run"
    registry = labctl.RunRegistry(
        runs={
            "replay-1": labctl.ManagedRun(
                run_id="replay-1",
                phase="replay",
                label="replay",
                status="completed",
                created_at="2026-04-18T00:00:00+00:00",
                owner_cmd=["python", "memory_replay.py"],
                workdir=str(tmp_path),
                output_dir=str(replay_out),
            )
        }
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr(labctl, "_load_registry", lambda: registry)
    monkeypatch.setattr(labctl, "_save_registry", lambda payload: captured.setdefault("registry", payload))
    monkeypatch.setattr(labctl, "_append_event", lambda event: captured.setdefault("event", event))
    monkeypatch.setattr(labctl, "_resolve_model_provider", lambda model: "openai-codex" if model else None)

    args = SimpleNamespace(
        label="bench",
        output_dir=str(tmp_path / "bench"),
        runset="real_ask",
        item=[],
        all_items=False,
        replay_dir=[f"syke:{replay_out}"],
        ask_model=None,
        judge_model="gpt-5.4",
        ask_timeout=600,
        judge_timeout=900,
        jobs=1,
        depends_on=[],
    )

    run = labctl._submit_benchmark(args)

    assert run.deps == ["replay-1"]
    assert run.provider == "openai-codex"
    assert run.metadata["ask_provider"] is None
    assert run.metadata["judge_provider"] == "openai-codex"


def test_tick_registry_starts_queued_run_and_respects_global_limit(
    tmp_path: Path, monkeypatch
) -> None:
    labctl = _load_labctl_module()

    dep_run = labctl.ManagedRun(
        run_id="replay-1",
        phase="replay",
        label="replay",
        status="completed",
        created_at="2026-04-18T00:00:00+00:00",
        owner_cmd=["python", "memory_replay.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "replay-out"),
    )
    queued_one = labctl.ManagedRun(
        run_id="bench-1",
        phase="benchmark",
        label="bench-1",
        status="queued",
        created_at="2026-04-18T00:01:00+00:00",
        owner_cmd=["python", "benchmark_runner.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "bench-one"),
        deps=["replay-1"],
    )
    queued_two = labctl.ManagedRun(
        run_id="bench-2",
        phase="benchmark",
        label="bench-2",
        status="queued",
        created_at="2026-04-18T00:02:00+00:00",
        owner_cmd=["python", "benchmark_runner.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "bench-two"),
        deps=["replay-1"],
    )
    registry = labctl.RunRegistry(
        scheduler={
            "global_max_running": 1,
            "replay_max_running": 3,
            "benchmark_max_running": 2,
            "judge_only_max_running": 2,
            "by_provider": {},
            "by_provider_model": {},
        },
        runs={
            dep_run.run_id: dep_run,
            queued_one.run_id: queued_one,
            queued_two.run_id: queued_two,
        },
    )

    saved: dict[str, object] = {}
    events: list[dict[str, object]] = []

    monkeypatch.setattr(labctl, "_load_registry", lambda: registry)
    monkeypatch.setattr(labctl, "_save_registry", lambda payload: saved.setdefault("registry", payload))
    monkeypatch.setattr(labctl, "_append_event", lambda event: events.append(event))

    class _FakeProc:
        pid = 4242

    popen_envs: list[dict[str, str]] = []

    def _fake_popen(*args, **kwargs):
        popen_envs.append(kwargs.get("env", {}))
        return _FakeProc()

    monkeypatch.setattr(labctl.subprocess, "Popen", _fake_popen)

    out = labctl.tick_registry()

    assert out.runs["bench-1"].status == "running"
    assert out.runs["bench-1"].pid == 4242
    assert out.runs["bench-2"].status == "queued"
    assert any(event["event"] == "started" and event["run_id"] == "bench-1" for event in events)
    assert isinstance(popen_envs[0], dict)


def test_tick_registry_injects_syke_provider_for_run(tmp_path: Path, monkeypatch) -> None:
    labctl = _load_labctl_module()

    queued = labctl.ManagedRun(
        run_id="bench-provider",
        phase="benchmark",
        label="bench-provider",
        status="queued",
        created_at="2026-04-18T00:00:00+00:00",
        owner_cmd=["python", "benchmark_runner.py"],
        workdir=str(tmp_path),
        output_dir=str(tmp_path / "bench-provider"),
        provider="openai-codex",
    )
    registry = labctl.RunRegistry(runs={queued.run_id: queued})

    monkeypatch.setattr(labctl, "_load_registry", lambda: registry)
    monkeypatch.setattr(labctl, "_save_registry", lambda payload: None)
    monkeypatch.setattr(labctl, "_append_event", lambda event: None)

    class _FakeProc:
        pid = 5555

    captured: dict[str, object] = {}

    def _fake_popen(*args, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return _FakeProc()

    monkeypatch.setattr(labctl.subprocess, "Popen", _fake_popen)

    out = labctl.tick_registry()

    assert out.runs["bench-provider"].status == "running"
    assert captured["env"]["SYKE_PROVIDER"] == "openai-codex"
