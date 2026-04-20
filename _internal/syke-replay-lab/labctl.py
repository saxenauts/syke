#!/usr/bin/env python3
"""Thin run manager for Syke Replay Lab.

This wraps the existing replay and benchmark entrypoints with:
- a local run registry
- dependency tracking
- provider-aware scheduling
- progress + ETA
- cancellation
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

LAB_ROOT = Path(__file__).resolve().parent
REPO_ROOT = LAB_ROOT.parents[1]
RUNS_ROOT = LAB_ROOT / "runs"
REGISTRY_PATH = RUNS_ROOT / "run_registry.json"
EVENTS_PATH = RUNS_ROOT / "run_events.jsonl"
LOGS_ROOT = RUNS_ROOT / "_manager_logs"

DEFAULT_SCHEDULER = {
    "global_max_running": 3,
    "global_max_slots": 12,
    "replay_max_running": 3,
    "replay_max_slots": 3,
    "benchmark_max_running": 2,
    "benchmark_max_slots": 12,
    "judge_only_max_running": 2,
    "judge_only_max_slots": 2,
    "by_provider": {},
    "by_provider_slots": {},
    "by_provider_model": {},
    "by_provider_model_slots": {},
}


class ProgressSnapshot(BaseModel):
    completed_units: int = 0
    total_units: int = 0
    unit_label: str = "units"
    rate_per_min: float = 0.0
    eta_seconds: int | None = None
    last_successful_unit: str | None = None
    partial: bool = True
    message: str | None = None


class FailureRecord(BaseModel):
    klass: str
    summary: str
    detail: str | None = None
    retryable: bool = False
    first_seen_at: str


RunPhase = Literal["replay", "benchmark", "judge_only"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled", "stale"]


class ManagedRun(BaseModel):
    run_id: str
    phase: RunPhase
    label: str
    status: RunStatus
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    heartbeat_at: str | None = None
    owner_cmd: list[str]
    workdir: str
    output_dir: str
    pid: int | None = None
    process_group: int | None = None
    provider: str | None = None
    model: str | None = None
    deps: list[str] = Field(default_factory=list)
    resume_supported: bool = True
    progress: ProgressSnapshot = Field(default_factory=ProgressSnapshot)
    failure: FailureRecord | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRegistry(BaseModel):
    version: int = 1
    scheduler: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_SCHEDULER))
    runs: dict[str, ManagedRun] = Field(default_factory=dict)


ProgressSnapshot.model_rebuild()
FailureRecord.model_rebuild()
ManagedRun.model_rebuild()
RunRegistry.model_rebuild()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _runner_python() -> str:
    override = os.environ.get("SYKE_LAB_PYTHON")
    if override:
        return str(Path(override).expanduser())

    repo_venv = REPO_ROOT / ".venv" / "bin" / "python"
    if repo_venv.exists():
        return str(repo_venv)

    return sys.executable


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _load_registry() -> RunRegistry:
    if not REGISTRY_PATH.exists():
        return RunRegistry()
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return RunRegistry.model_validate(payload)


def _save_registry(registry: RunRegistry) -> None:
    _write_json_atomic(REGISTRY_PATH, registry.model_dump(mode="json"))


def _build_run_id(phase: RunPhase, label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in label).strip("-") or phase
    return f"{phase}-{safe}-{stamp}-{uuid4().hex[:6]}"


def _status_config_path(output_dir: Path) -> Path:
    return output_dir / "config.json"


def _status_results_path(output_dir: Path) -> Path:
    return output_dir / "results.json"


def _status_ask_results_path(output_dir: Path) -> Path:
    return output_dir / "ask_results.json"


def _status_run_status_path(output_dir: Path) -> Path:
    return output_dir / "run_status.json"


def _status_replay_path(output_dir: Path) -> Path:
    return output_dir / "replay_results.json"


def _infer_deps_from_paths(registry: RunRegistry, paths: list[Path]) -> list[str]:
    deps: list[str] = []
    for path in paths:
        resolved = str(path.resolve())
        for run_id, run in registry.runs.items():
            if str(Path(run.output_dir).resolve()) == resolved and run_id not in deps:
                deps.append(run_id)
    return deps


def _build_replay_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        _runner_python(),
        str(LAB_ROOT / "memory_replay.py"),
        "--bundle",
        str(Path(args.bundle).resolve()),
        "--output-dir",
        str(Path(args.output_dir).resolve()),
        "--user-id",
        args.user_id,
        "--condition",
        args.condition,
    ]
    if args.max_days is not None:
        cmd.extend(["--max-days", str(args.max_days)])
    if args.start_day:
        cmd.extend(["--start-day", args.start_day])
    if args.cycles_per_day is not None:
        cmd.extend(["--cycles-per-day", str(args.cycles_per_day)])
    if args.model:
        cmd.extend(["--model", args.model])
    if args.provider:
        cmd.extend(["--provider", args.provider])
    return cmd


def _build_benchmark_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        _runner_python(),
        str(LAB_ROOT / "benchmark_runner.py"),
        "--output-dir",
        str(Path(args.output_dir).resolve()),
    ]
    if args.runset:
        cmd.extend(["--runset", args.runset])
    for item in args.item or []:
        cmd.extend(["--item", item])
    if args.all_items:
        cmd.append("--all-items")
    for replay_dir in args.replay_dir or []:
        cmd.extend(["--replay-dir", replay_dir])
    if args.ask_model:
        cmd.extend(["--ask-model", args.ask_model])
    if getattr(args, "ask_provider", None):
        cmd.extend(["--ask-provider", args.ask_provider])
    if args.judge_model:
        cmd.extend(["--judge-model", args.judge_model])
    if getattr(args, "judge_provider", None):
        cmd.extend(["--judge-provider", args.judge_provider])
    if args.ask_timeout is not None:
        cmd.extend(["--ask-timeout", str(args.ask_timeout)])
    if args.judge_timeout is not None:
        cmd.extend(["--judge-timeout", str(args.judge_timeout)])
    if args.jobs is not None:
        cmd.extend(["--jobs", str(args.jobs)])
    return cmd


def _build_judge_only_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        _runner_python(),
        str(LAB_ROOT / "benchmark_runner.py"),
        "--judge-only-from",
        str(Path(args.judge_only_from).resolve()),
        "--output-dir",
        str(Path(args.output_dir).resolve()),
    ]
    if args.runset:
        cmd.extend(["--runset", args.runset])
    for item in args.item or []:
        cmd.extend(["--item", item])
    if args.all_items:
        cmd.append("--all-items")
    if args.judge_model:
        cmd.extend(["--judge-model", args.judge_model])
    if getattr(args, "judge_provider", None):
        cmd.extend(["--judge-provider", args.judge_provider])
    if args.judge_timeout is not None:
        cmd.extend(["--judge-timeout", str(args.judge_timeout)])
    return cmd


def _resolve_model_provider(model: str | None) -> str | None:
    if not model:
        return None
    try:
        from syke.llm.pi_client import resolve_pi_provider

        return resolve_pi_provider(model)
    except Exception:
        return None


def _submit_run(run: ManagedRun) -> ManagedRun:
    registry = _load_registry()
    registry.runs[run.run_id] = run
    _save_registry(registry)
    _append_event({"ts": _now_iso(), "event": "submit", "run_id": run.run_id, "phase": run.phase})
    return run


def _count_running(registry: RunRegistry, *, phase: str | None = None, provider: str | None = None, model: str | None = None) -> int:
    count = 0
    for run in registry.runs.values():
        if run.status != "running":
            continue
        if phase and run.phase != phase:
            continue
        if provider and run.provider != provider:
            continue
        if model and run.model != model:
            continue
        count += 1
    return count


def _slot_demand(run: ManagedRun) -> int:
    try:
        return max(1, int((run.metadata or {}).get("slot_demand") or 1))
    except Exception:
        return 1


def _provider_slot_demands(run: ManagedRun) -> list[tuple[str, int]]:
    demand = _slot_demand(run)
    ask_provider = (run.metadata or {}).get("ask_provider")
    judge_provider = (run.metadata or {}).get("judge_provider")
    out: list[tuple[str, int]] = []
    if isinstance(ask_provider, str) and ask_provider:
        out.append((ask_provider, demand // 2 or 1))
    if isinstance(judge_provider, str) and judge_provider:
        if ask_provider:
            out.append((judge_provider, max(1, demand - (demand // 2 or 1))))
        else:
            out.append((judge_provider, demand))
    if not out and run.provider and run.provider != "mixed":
        out.append((run.provider, demand))
    return out


def _provider_model_slot_demands(run: ManagedRun) -> list[tuple[str, str, int]]:
    demand = _slot_demand(run)
    ask_provider = (run.metadata or {}).get("ask_provider")
    judge_provider = (run.metadata or {}).get("judge_provider")
    ask_model = (run.metadata or {}).get("ask_model")
    judge_model = (run.metadata or {}).get("judge_model")
    out: list[tuple[str, str, int]] = []
    if isinstance(ask_provider, str) and ask_provider and isinstance(ask_model, str) and ask_model:
        out.append((ask_provider, ask_model, demand // 2 or 1))
    if isinstance(judge_provider, str) and judge_provider and isinstance(judge_model, str) and judge_model:
        if ask_provider and ask_model:
            out.append((judge_provider, judge_model, max(1, demand - (demand // 2 or 1))))
        else:
            out.append((judge_provider, judge_model, demand))
    if not out and run.provider and run.provider != "mixed" and run.model:
        out.append((run.provider, run.model, demand))
    return out


def _count_running_provider_slots(registry: RunRegistry, provider: str) -> int:
    total = 0
    for run in registry.runs.values():
        if run.status != "running":
            continue
        for p, slots in _provider_slot_demands(run):
            if p == provider:
                total += slots
    return total


def _count_running_provider_model_slots(registry: RunRegistry, provider: str, model: str) -> int:
    total = 0
    for run in registry.runs.values():
        if run.status != "running":
            continue
        for p, m, slots in _provider_model_slot_demands(run):
            if p == provider and m == model:
                total += slots
    return total


def _count_running_slots(
    registry: RunRegistry,
    *,
    phase: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    total = 0
    for run in registry.runs.values():
        if run.status != "running":
            continue
        if phase and run.phase != phase:
            continue
        if provider and run.provider != provider:
            continue
        if model and run.model != model:
            continue
        total += _slot_demand(run)
    return total


def _deps_satisfied(registry: RunRegistry, run: ManagedRun) -> bool:
    for dep in run.deps:
        dep_run = registry.runs.get(dep)
        if dep_run is None or dep_run.status != "completed":
            return False
    return True


def _classify_failure(output_dir: Path) -> FailureRecord:
    replay_path = _status_replay_path(output_dir)
    results_path = _status_results_path(output_dir)
    config_path = _status_config_path(output_dir)
    now = _now_iso()
    if replay_path.exists():
        payload = json.loads(replay_path.read_text(encoding="utf-8"))
        meta = payload.get("metadata", {})
        error = str(meta.get("error") or "replay failed")
        return FailureRecord(klass="runtime_failure", summary=error, detail=error, retryable=False, first_seen_at=now)
    if results_path.exists() and config_path.exists():
        return FailureRecord(klass="stale_run", summary="process exited before run completed", detail=None, retryable=True, first_seen_at=now)
    return FailureRecord(klass="worker_crash", summary="process exited without progress artifacts", detail=None, retryable=True, first_seen_at=now)


def _extract_progress(run: ManagedRun) -> ProgressSnapshot:
    output_dir = Path(run.output_dir)
    run_status_path = _status_run_status_path(output_dir)
    if run_status_path.exists():
        try:
            payload = json.loads(run_status_path.read_text(encoding="utf-8"))
            return ProgressSnapshot(
                completed_units=int(payload.get("completed_units") or 0),
                total_units=int(payload.get("total_units") or 0),
                unit_label=str(payload.get("unit_label") or "units"),
                eta_seconds=int(payload["eta_seconds"]) if payload.get("eta_seconds") is not None else None,
                last_successful_unit=str(payload.get("last_successful_unit")) if payload.get("last_successful_unit") else None,
                partial=bool(payload.get("partial", True)),
                message=str(payload.get("message") or "running"),
            )
        except Exception:
            pass
    if run.phase == "replay":
        replay_path = _status_replay_path(output_dir)
        if not replay_path.exists():
            return ProgressSnapshot(message="waiting for replay_results.json")
        payload = json.loads(replay_path.read_text(encoding="utf-8"))
        meta = payload.get("metadata", {})
        completed = int(meta.get("completed_cycles") or 0)
        total = int(meta.get("selected_replay_cycles") or 0)
        started_at = meta.get("started_at")
        rate = 0.0
        eta = None
        if completed > 0 and isinstance(started_at, str) and total > completed:
            ds = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            elapsed_min = max((datetime.now(UTC) - ds).total_seconds() / 60.0, 1e-6)
            rate = completed / elapsed_min
            eta = int(((total - completed) / rate) * 60) if rate > 0 else None
        return ProgressSnapshot(
            completed_units=completed,
            total_units=total,
            unit_label="cycles",
            rate_per_min=rate,
            eta_seconds=eta,
            last_successful_unit=str(meta.get("last_completed_day") or "") or None,
            partial=bool(meta.get("partial", True)),
            message=str(meta.get("status") or "running"),
        )

    config_path = _status_config_path(output_dir)
    results_path = _status_results_path(output_dir)
    ask_results_path = _status_ask_results_path(output_dir)
    completed = 0
    if results_path.exists():
        try:
            completed = len(json.loads(results_path.read_text(encoding="utf-8")))
        except Exception:
            completed = 0
    ask_completed = 0
    if ask_results_path.exists():
        try:
            ask_completed = len(json.loads(ask_results_path.read_text(encoding="utf-8")))
        except Exception:
            ask_completed = 0
    if not config_path.exists():
        return ProgressSnapshot(completed_units=completed, message="waiting for config/results")
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    probes = cfg.get("probes") or []
    conditions = cfg.get("conditions") or []
    if run.phase == "judge_only":
        total = len(probes) * len(conditions) if isinstance(probes, list) and isinstance(conditions, list) else int(cfg.get("total_evaluations") or 0)
        label = "reruns"
    else:
        total = len(probes) * len(conditions) if isinstance(probes, list) and isinstance(conditions, list) else 0
        label = "rollouts"
    started_at = cfg.get("started_at")
    rate = 0.0
    eta = None
    if completed > 0 and total > completed and isinstance(started_at, str):
        ds = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        elapsed_min = max((datetime.now(UTC) - ds).total_seconds() / 60.0, 1e-6)
        rate = completed / elapsed_min
        eta = int(((total - completed) / rate) * 60) if rate > 0 else None
    return ProgressSnapshot(
        completed_units=completed,
        total_units=total,
        unit_label=label,
        rate_per_min=rate,
        eta_seconds=eta,
        last_successful_unit=str(completed) if completed else None,
        partial=completed < total if total else True,
        message=(
            f"asks {ask_completed}/{total}, judges {completed}/{total}"
            if total
            else ("running" if completed < total else "completed")
        ),
    )


def _process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tick_registry() -> RunRegistry:
    registry = _load_registry()
    now = _now_iso()

    # Reconcile existing runs.
    for run in registry.runs.values():
        output_dir = Path(run.output_dir)
        if run.status == "running":
            run.progress = _extract_progress(run)
            run.heartbeat_at = now
            replay_path = _status_replay_path(output_dir)
            bench_path = output_dir / "benchmark_results.json"
            if replay_path.exists():
                meta = json.loads(replay_path.read_text(encoding="utf-8")).get("metadata", {})
                if meta.get("status") == "completed":
                    run.status = "completed"
                    run.completed_at = now
                    run.heartbeat_at = now
                    _append_event({"ts": now, "event": "completed", "run_id": run.run_id})
                    continue
                if meta.get("status") == "failed":
                    run.status = "failed"
                    run.completed_at = now
                    run.failure = FailureRecord(
                        klass="runtime_failure",
                        summary=str(meta.get("error") or "replay failed"),
                        detail=str(meta.get("error") or ""),
                        retryable=False,
                        first_seen_at=now,
                    )
                    _append_event({"ts": now, "event": "failed", "run_id": run.run_id, "class": run.failure.klass})
                    continue
            elif bench_path.exists():
                run.status = "completed"
                run.completed_at = now
                _append_event({"ts": now, "event": "completed", "run_id": run.run_id})
                continue
            if not _process_alive(run.pid):
                run.status = "stale"
                run.completed_at = now
                run.failure = _classify_failure(output_dir)
                _append_event({"ts": now, "event": "stale", "run_id": run.run_id, "class": run.failure.klass})

    # Start queued runs if capacity exists.
    for run in sorted(registry.runs.values(), key=lambda r: r.created_at):
        if run.status != "queued":
            continue
        if not _deps_satisfied(registry, run):
            continue
        sched = registry.scheduler
        if _count_running(registry) >= int(sched.get("global_max_running", 3)):
            break
        if _count_running_slots(registry) + _slot_demand(run) > int(sched.get("global_max_slots", 12)):
            continue
        if run.phase == "replay" and _count_running(registry, phase="replay") >= int(sched.get("replay_max_running", 3)):
            continue
        if run.phase == "replay" and _count_running_slots(registry, phase="replay") + _slot_demand(run) > int(sched.get("replay_max_slots", 3)):
            continue
        if run.phase == "benchmark" and _count_running(registry, phase="benchmark") >= int(sched.get("benchmark_max_running", 2)):
            continue
        if run.phase == "benchmark" and _count_running_slots(registry, phase="benchmark") + _slot_demand(run) > int(sched.get("benchmark_max_slots", 12)):
            continue
        if run.phase == "judge_only" and _count_running(registry, phase="judge_only") >= int(sched.get("judge_only_max_running", 2)):
            continue
        if run.phase == "judge_only" and _count_running_slots(registry, phase="judge_only") + _slot_demand(run) > int(sched.get("judge_only_max_slots", 2)):
            continue
        if run.provider:
            provider_limits = sched.get("by_provider", {})
            limit = provider_limits.get(run.provider)
            if limit is not None and _count_running(registry, provider=run.provider) >= int(limit):
                continue
            provider_slot_limits = sched.get("by_provider_slots", {})
            limit = provider_slot_limits.get(run.provider)
            if (
                run.provider != "mixed"
                and limit is not None
                and _count_running_provider_slots(registry, run.provider) + _slot_demand(run) > int(limit)
            ):
                continue
        if run.provider and run.model:
            combo_limits = sched.get("by_provider_model", {})
            key = f"{run.provider}:{run.model}"
            limit = combo_limits.get(key)
            if limit is not None and _count_running(registry, provider=run.provider, model=run.model) >= int(limit):
                continue
            combo_slot_limits = sched.get("by_provider_model_slots", {})
            limit = combo_slot_limits.get(key)
            if (
                run.provider != "mixed"
                and limit is not None
                and _count_running_provider_model_slots(registry, run.provider, run.model) + _slot_demand(run) > int(limit)
            ):
                continue
        if run.provider == "mixed":
            provider_slot_limits = sched.get("by_provider_slots", {})
            blocked = False
            for provider_name, slots in _provider_slot_demands(run):
                limit = provider_slot_limits.get(provider_name)
                if limit is not None and _count_running_provider_slots(registry, provider_name) + slots > int(limit):
                    blocked = True
                    break
            if blocked:
                continue
            combo_slot_limits = sched.get("by_provider_model_slots", {})
            for provider_name, model_name, slots in _provider_model_slot_demands(run):
                limit = combo_slot_limits.get(f"{provider_name}:{model_name}")
                if limit is not None and _count_running_provider_model_slots(registry, provider_name, model_name) + slots > int(limit):
                    blocked = True
                    break
            if blocked:
                continue

        LOGS_ROOT.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_ROOT / f"{run.run_id}.log"
        handle = log_path.open("ab")
        env = os.environ.copy()
        if run.provider and run.provider != "mixed":
            env["SYKE_PROVIDER"] = run.provider
        proc = subprocess.Popen(
            run.owner_cmd,
            cwd=run.workdir,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        run.pid = proc.pid
        run.process_group = proc.pid
        run.status = "running"
        run.started_at = now
        run.heartbeat_at = now
        _append_event({"ts": now, "event": "started", "run_id": run.run_id, "pid": run.pid})

    _save_registry(registry)
    return registry


def _print_status(registry: RunRegistry, *, active_only: bool = False) -> None:
    runs = list(registry.runs.values())
    if active_only:
        runs = [run for run in runs if run.status in {"queued", "running", "stale"}]
    print(
        f"{'run_id':28s} {'phase':10s} {'status':10s} {'provider/model':28s} {'progress':16s} {'eta':8s} {'deps':4s}"
    )
    print("-" * 120)
    for run in sorted(runs, key=lambda r: r.created_at):
        provider_model = f"{run.provider or '-'} / {run.model or '-'}"
        prog = f"{run.progress.completed_units}/{run.progress.total_units} {run.progress.unit_label}"
        eta = f"{run.progress.eta_seconds}s" if run.progress.eta_seconds is not None else "-"
        print(
            f"{run.run_id[:28]:28s} {run.phase:10s} {run.status:10s} {provider_model[:28]:28s} {prog[:16]:16s} {eta:8s} {len(run.deps):4d}"
        )


def _submit_replay(args: argparse.Namespace) -> ManagedRun:
    registry = _load_registry()
    output_dir = Path(args.output_dir).resolve()
    run = ManagedRun(
        run_id=_build_run_id("replay", args.label),
        phase="replay",
        label=args.label,
        status="queued",
        created_at=_now_iso(),
        owner_cmd=_build_replay_cmd(args),
        workdir=str(Path.cwd()),
        output_dir=str(output_dir),
        provider=args.provider,
        model=args.model,
        deps=args.depends_on or [],
        metadata={"bundle": str(Path(args.bundle).resolve()), "condition": args.condition, "slot_demand": 1},
    )
    return _submit_run(run)


def _submit_benchmark(args: argparse.Namespace) -> ManagedRun:
    registry = _load_registry()
    replay_paths: list[Path] = []
    for entry in args.replay_dir or []:
        if ":" in entry:
            _, path_str = entry.rsplit(":", 1)
            replay_paths.append(Path(path_str))
    deps = list(args.depends_on or []) + _infer_deps_from_paths(registry, replay_paths)
    ask_provider = _resolve_model_provider(args.ask_model) if args.ask_model else None
    judge_provider = _resolve_model_provider(args.judge_model) if args.judge_model else None
    launch_provider = ask_provider or judge_provider or os.environ.get("SYKE_PROVIDER")
    if ask_provider and judge_provider and ask_provider != judge_provider:
        launch_provider = "mixed"
    run = ManagedRun(
        run_id=_build_run_id("benchmark", args.label),
        phase="benchmark",
        label=args.label,
        status="queued",
        created_at=_now_iso(),
        owner_cmd=_build_benchmark_cmd(args),
        workdir=str(Path.cwd()),
        output_dir=str(Path(args.output_dir).resolve()),
        provider=launch_provider,
        model=args.ask_model or args.judge_model,
        deps=deps,
        metadata={
            "replay_dirs": list(args.replay_dir or []),
            "runset": args.runset,
            "ask_model": args.ask_model,
            "ask_provider": ask_provider,
            "judge_model": args.judge_model,
            "judge_provider": judge_provider,
            "slot_demand": max(1, int(args.jobs) * 2),
        },
    )
    return _submit_run(run)


def _submit_judge_only(args: argparse.Namespace) -> ManagedRun:
    registry = _load_registry()
    source_run = Path(args.judge_only_from).resolve()
    deps = list(args.depends_on or []) + _infer_deps_from_paths(registry, [source_run])
    judge_provider = _resolve_model_provider(args.judge_model)
    run = ManagedRun(
        run_id=_build_run_id("judge_only", args.label),
        phase="judge_only",
        label=args.label,
        status="queued",
        created_at=_now_iso(),
        owner_cmd=_build_judge_only_cmd(args),
        workdir=str(Path.cwd()),
        output_dir=str(Path(args.output_dir).resolve()),
        provider=judge_provider or os.environ.get("SYKE_PROVIDER"),
        model=args.judge_model,
        deps=deps,
        metadata={
            "judge_only_from": str(source_run),
            "runset": args.runset,
            "judge_provider": judge_provider,
            "slot_demand": 1,
        },
    )
    return _submit_run(run)


def _cancel_run(run_id: str) -> ManagedRun:
    registry = _load_registry()
    run = registry.runs[run_id]
    if run.process_group:
        try:
            os.killpg(run.process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    run.status = "cancelled"
    run.completed_at = _now_iso()
    run.failure = FailureRecord(
        klass="cancelled",
        summary="cancelled by operator",
        retryable=False,
        first_seen_at=run.completed_at,
    )
    _save_registry(registry)
    _append_event({"ts": run.completed_at, "event": "cancelled", "run_id": run.run_id})
    return run


def _retry_run(run_id: str) -> ManagedRun:
    registry = _load_registry()
    old = registry.runs[run_id]
    new = ManagedRun(
        run_id=_build_run_id(old.phase, old.label),
        phase=old.phase,
        label=old.label,
        status="queued",
        created_at=_now_iso(),
        owner_cmd=list(old.owner_cmd),
        workdir=old.workdir,
        output_dir=old.output_dir,
        provider=old.provider,
        model=old.model,
        deps=list(old.deps),
        resume_supported=old.resume_supported,
        metadata=dict(old.metadata),
    )
    return _submit_run(new)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thin run manager for Syke Replay Lab")
    sub = parser.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("submit-replay")
    replay.add_argument("--label", required=True)
    replay.add_argument("--bundle", required=True)
    replay.add_argument("--output-dir", required=True)
    replay.add_argument("--user-id", default="replay")
    replay.add_argument("--condition", default="syke")
    replay.add_argument("--max-days", type=int)
    replay.add_argument("--start-day")
    replay.add_argument("--cycles-per-day", type=int, default=1)
    replay.add_argument("--model")
    replay.add_argument("--provider")
    replay.add_argument("--depends-on", action="append", default=[])

    bench = sub.add_parser("submit-benchmark")
    bench.add_argument("--label", required=True)
    bench.add_argument("--output-dir", required=True)
    bench.add_argument("--runset")
    bench.add_argument("--item", action="append", default=[])
    bench.add_argument("--all-items", action="store_true")
    bench.add_argument("--replay-dir", action="append", default=[])
    bench.add_argument("--ask-model")
    bench.add_argument("--ask-provider")
    bench.add_argument("--judge-model", default="gpt-5.4")
    bench.add_argument("--judge-provider")
    bench.add_argument("--ask-timeout", type=int, default=600)
    bench.add_argument("--judge-timeout", type=int, default=900)
    bench.add_argument("--jobs", type=int, default=1)
    bench.add_argument("--depends-on", action="append", default=[])

    judge = sub.add_parser("submit-judge-only")
    judge.add_argument("--label", required=True)
    judge.add_argument("--judge-only-from", required=True)
    judge.add_argument("--output-dir", required=True)
    judge.add_argument("--runset")
    judge.add_argument("--item", action="append", default=[])
    judge.add_argument("--all-items", action="store_true")
    judge.add_argument("--judge-model", default="gpt-5.4")
    judge.add_argument("--judge-provider")
    judge.add_argument("--judge-timeout", type=int, default=900)
    judge.add_argument("--depends-on", action="append", default=[])

    sub.add_parser("tick")
    status = sub.add_parser("status")
    status.add_argument("--active-only", action="store_true")
    status.add_argument("--json", action="store_true")

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=5.0)
    watch.add_argument("--active-only", action="store_true")

    cancel = sub.add_parser("cancel")
    cancel.add_argument("run_id")

    retry = sub.add_parser("retry")
    retry.add_argument("run_id")

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--interval", type=float, default=5.0,
                       help="tick_registry() interval in seconds")
    serve.add_argument("--no-open", action="store_true",
                       help="don't auto-open the browser")

    return parser.parse_args()


def _run_serve(host: str, port: int, interval: float, auto_open: bool) -> None:
    """HTTP server for the lab dir + background ticker thread.

    One command gives the viz a live registry: the thread calls tick_registry()
    every `interval` seconds, so runs_viz.html's polling of run_registry.json
    reflects real progress without a separate `labctl watch` session.
    """
    import http.server
    import socketserver
    import threading
    import webbrowser

    stop_event = threading.Event()

    def ticker() -> None:
        while not stop_event.is_set():
            try:
                tick_registry()
            except Exception as exc:
                print(f"[ticker] error: {exc}", file=sys.stderr)
            stop_event.wait(interval)

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt: str, *a: Any) -> None:  # keep stdout clean
            return

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

    class ReusableTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    os.chdir(LAB_ROOT)

    server = ReusableTCPServer((host, port), QuietHandler)
    t = threading.Thread(target=ticker, name="labctl-ticker", daemon=True)
    t.start()

    url = f"http://{host}:{port}/runs_viz.html"
    print(f"labctl serve · ticker every {interval}s · {url}")
    if auto_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()


def main() -> None:
    args = _parse_args()
    if args.command == "submit-replay":
        run = _submit_replay(args)
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return
    if args.command == "submit-benchmark":
        run = _submit_benchmark(args)
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return
    if args.command == "submit-judge-only":
        run = _submit_judge_only(args)
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return
    if args.command == "tick":
        registry = tick_registry()
        print(json.dumps(registry.model_dump(mode="json"), indent=2))
        return
    if args.command == "status":
        registry = tick_registry()
        if args.json:
            print(json.dumps(registry.model_dump(mode="json"), indent=2))
        else:
            _print_status(registry, active_only=args.active_only)
        return
    if args.command == "watch":
        while True:
            registry = tick_registry()
            os.system("clear")
            _print_status(registry, active_only=args.active_only)
            time.sleep(args.interval)
        return
    if args.command == "cancel":
        run = _cancel_run(args.run_id)
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return
    if args.command == "retry":
        run = _retry_run(args.run_id)
        print(json.dumps(run.model_dump(mode="json"), indent=2))
        return
    if args.command == "serve":
        _run_serve(args.host, args.port, args.interval, auto_open=not args.no_open)
        return
    raise SystemExit(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    main()
