#!/usr/bin/env python3
"""Replay Sandbox — bundle-based evaluation for Syke's memory pipeline.

Runs a materialized replay bundle through the synthesis pipeline one observed
day at a time, starting from empty state. The bundle provides raw harness files
plus metadata describing the replay window. Each cycle snapshots the memex and
records metrics.

See docs/RUNTIME_AND_REPLAY.md for the current replay workflow.

Window semantics:
    --max-days N   => take the first N bundle days after any start-day filter
    --start-day D  => start at the first bundle day >= D

Usage:
    python _internal/syke-replay-lab/memory_replay.py \
        --bundle /path/to/materialized_bundle \
        --output-dir /tmp/replay_output \
        --user-id replay_v1 \
        --dry-run

    python _internal/syke-replay-lab/memory_replay.py \
        --bundle /path/to/materialized_bundle \
        --output-dir /tmp/replay_output \
        --user-id replay_v1 \
        --max-days 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys as _sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from syke.db import SykeDB
from syke.llm.backends.pi_synthesis import pi_synthesize as synthesize

# Per-cycle time-travel slicer (sibling module in the replay lab)
_LAB_DIR = Path(__file__).resolve().parent
if str(_LAB_DIR) not in _sys.path:
    _sys.path.insert(0, str(_LAB_DIR))
from cycle_slicer import slice_bundle  # noqa: E402

log = logging.getLogger(__name__)


def _json_safe(obj: Any) -> Any:
    """Coerce non-JSON types that can leak from SQLite (BLOB → bytes)."""
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            import base64

            return {"__bytes_b64__": base64.b64encode(obj).decode("ascii")}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Avoid half-written JSON while the lab is polling active runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, default=_json_safe))
    tmp_path.replace(path)


def _persist_run_checkpoint(output_dir: Path, result_data: dict[str, Any]) -> None:
    """Write the run export after every durable checkpoint."""
    _write_json_atomic(output_dir / "replay_results.json", result_data)


def _load_run_checkpoint(output_dir: Path) -> dict[str, Any]:
    checkpoint_path = output_dir / "replay_results.json"
    if not checkpoint_path.exists():
        raise FileNotFoundError(f"Replay checkpoint not found: {checkpoint_path}")
    payload = json.loads(checkpoint_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"Replay checkpoint is not a JSON object: {checkpoint_path}")
    return payload


def _set_run_phase(
    metadata: dict[str, Any],
    *,
    phase: str,
    cycle: int | None,
    day: str | None,
) -> None:
    metadata["phase"] = phase
    metadata["active_cycle"] = cycle
    metadata["active_day"] = day
    metadata["heartbeat_at"] = datetime.now(UTC).isoformat()


# Zero condition: substrate-only memory update. Keep the same identity and
# durable-state substrate, but remove the richer synthesis/control surface.
# The zero condition should still track user work state from the frozen
# evidence; it should not optimize for generic workspace/bootstrap upkeep.
_ZERO_PROMPT = """
Read the current frozen workspace evidence and update durable state only where
it will help future cycles recover the user's actual work.

Prioritize:
- active work threads
- concrete artifacts and file paths
- decisions, blockers, and state transitions
- what is live now versus residue

Do not optimize for:
- generic workspace maintenance
- adapter inventories
- syke.db row counts
- bootstrap/admin state unless it is itself the user's live work

Keep the update minimal and evidence-bound.
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a materialized bundle through the full Syke pipeline"
    )
    parser.add_argument("--bundle", required=True, help="Path to materialized bundle directory")
    parser.add_argument("--output-dir", required=True, help="Directory for replay DB + results")
    parser.add_argument("--user-id", default="replay", help="User ID for replay")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count days/cycles without running synthesis",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        help="Stop after N observed event days after any --start-day filter",
    )
    parser.add_argument(
        "--cycles-per-day",
        type=int,
        default=1,
        help="Split each observed day into N sequential replay cycles by event order",
    )
    parser.add_argument(
        "--start-day",
        help="Start from the first observed day >= this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--condition",
        default="production",
        choices=["production", "zero"],
        help="Prompt condition for ablation",
    )
    parser.add_argument(
        "--skill",
        metavar="FILE",
        help="Path to custom skill/prompt file (overrides --condition and synthesis.md)",
    )
    parser.add_argument(
        "--model",
        help="Override model for this replay run",
    )
    parser.add_argument(
        "--provider",
        help=(
            "Override provider for replay "
            "(e.g., azure-openai-responses). Does NOT change live install."
        ),
    )
    parser.add_argument(
        "--api-key",
        help="API key for the overridden provider",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL for the overridden provider",
    )
    return parser.parse_args()


def snapshot_memex(
    db: SykeDB,
    user_id: str,
    day: str,
    cycle_num: int,
    result: dict[str, Any] | None,
    *,
    cycle_key: str | None = None,
    cycle_cutoff_iso: str | None = None,
) -> dict[str, Any]:
    """Capture memex state and metrics after synthesis."""
    timeline_day = cycle_key or day
    memex = db.get_memex(user_id)
    content = memex["content"] if memex else ""

    # Count pointers (→ Memory: patterns)
    arrow_memory_pattern = len(re.findall(r"→\s*Memory:", content))
    memories_rows = db.conn.execute(
        """SELECT id, content, source_event_ids, created_at, updated_at
           FROM memories
           WHERE user_id = ? AND active = 1
           ORDER BY created_at DESC""",
        (user_id,),
    ).fetchall()
    link_rows = db.conn.execute(
        """SELECT id, source_id, target_id, reason, created_at
           FROM links
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,),
    ).fetchall()
    return {
        "day": timeline_day,
        "source_day": day,
        "cycle": cycle_num,
        "cycle_cutoff_iso": cycle_cutoff_iso,
        "memex_version": cycle_num,
        "memex_content": content,
        "chars": len(content),
        "sections": content.count("## "),
        "arrows_total": content.count("→"),
        "arrows_memory": arrow_memory_pattern,
        "memories_active": db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ? AND active = 1",
            (user_id,),
        ).fetchone()[0],
        "memories_total": db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "links_count": db.conn.execute(
            "SELECT COUNT(*) FROM links WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "cycle_records": db.conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0],
        "cost_usd": (result.get("cost_usd") or 0) if result else 0,
        "tool_calls": (result.get("tool_calls") or 0) if result else 0,
        "tool_name_counts": dict(result.get("tool_name_counts") or {}) if result else {},
        "turns": (result.get("num_turns") or 0) if result else 0,
        "input_tokens": (result.get("input_tokens") or 0) if result else 0,
        "output_tokens": (result.get("output_tokens") or 0) if result else 0,
        "cache_read_tokens": (result.get("cache_read_tokens") or 0) if result else 0,
        "duration_ms": (result.get("duration_ms") or 0) if result else 0,
        "status": result.get("status", "unknown") if result else "dry_run",
        "cursor": None,
        "memories": [dict(row) for row in memories_rows],
        "links": [dict(row) for row in link_rows],
        **_extract_latest_trace(db, user_id),
    }


def _extract_latest_trace(db: SykeDB, user_id: str) -> dict[str, Any]:
    """Pull the latest rollout trace for this cycle into the timeline entry."""
    try:
        row = db.conn.execute(
            """SELECT transcript, thinking, tool_calls, output_text
               FROM rollout_traces
               WHERE user_id = ? AND kind = 'synthesis'
               ORDER BY started_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        if row:
            return {
                "transcript": json.loads(row[0]) if row[0] and row[0] != "[]" else [],
                "thinking": json.loads(row[1]) if row[1] and row[1] != "[]" else [],
                "tool_calls_detail": json.loads(row[2]) if row[2] and row[2] != "[]" else [],
                "output_text": row[3] or "",
            }
    except Exception:
        pass
    return {"transcript": [], "thinking": [], "tool_calls_detail": [], "output_text": ""}


def save_memex_version(output_dir: Path, version: int, content: str) -> None:
    """Save memex content to a versioned markdown file."""
    memex_dir = output_dir / "memex"
    memex_dir.mkdir(parents=True, exist_ok=True)
    version_path = memex_dir / f"v{version:03d}.md"
    version_path.write_text(content)


def build_skill_override(condition: str) -> str | None:
    """Return the skill file content to use for this ablation condition.

    Returns None for production (use the real skill file).
    Passes the string to synthesize(skill_override=...) — no global state.
    """
    if condition == "zero":
        return _ZERO_PROMPT
    return None  # production


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _unlink_if_present(path: Path) -> None:
    if _path_present(path):
        path.unlink()


def _clear_stale_synthesis_lock(user_id: str) -> bool:
    """Replay should recover from dead cross-process locks without manual cleanup."""
    from syke.llm.backends.pi_synthesis import _synthesis_lock_path

    lock_path = _synthesis_lock_path(user_id)
    if not lock_path.exists():
        return False

    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False

    pid_text = raw.split("\t", 1)[0].strip()
    if not pid_text.isdigit():
        return False

    pid = int(pid_text)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        lock_path.unlink(missing_ok=True)
        log.info("Cleared stale replay synthesis lock: %s", lock_path)
        return True
    except PermissionError:
        return False

    return False


def _paths_match(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _validate_workspace_contract(
    workspace_root: Path,
    syke_db_path: Path,
    **_kwargs: Any,
) -> None:
    """Ensure replay workspace has a valid syke.db."""
    issues: list[str] = []

    if not syke_db_path.exists():
        issues.append(f"missing canonical DB: {syke_db_path}")

    if issues:
        joined = "; ".join(issues)
        raise RuntimeError(f"Replay workspace contract violation: {joined}")


_WORKSPACE_GLOBALS = ("WORKSPACE_ROOT", "SYKE_DB", "MEMEX_PATH", "SESSIONS_DIR")


def capture_workspace_bindings() -> dict[str, dict[str, Any]]:
    """Capture the current workspace bindings for later restoration."""
    from syke.llm.backends import pi_synthesis as pi_synthesis_module
    from syke.runtime import workspace as workspace_module

    return {
        "workspace": {name: getattr(workspace_module, name) for name in _WORKSPACE_GLOBALS},
        "pi_synthesis": {
            name: getattr(pi_synthesis_module, name)
            for name in _WORKSPACE_GLOBALS
            if hasattr(pi_synthesis_module, name)
        },
        "pi_synthesis_lock_path": {
            "_synthesis_lock_path": getattr(pi_synthesis_module, "_synthesis_lock_path", None),
        },
    }


def restore_workspace_bindings(snapshot: dict[str, dict[str, Any]]) -> None:
    """Restore workspace-related module globals after a replay run."""
    from syke.llm.backends import pi_synthesis as pi_synthesis_module
    from syke.runtime import workspace as workspace_module

    workspace_root = snapshot.get("workspace", {}).get("WORKSPACE_ROOT")
    if isinstance(workspace_root, Path):
        workspace_module.set_workspace_root(workspace_root)

    for name, value in snapshot.get("pi_synthesis", {}).items():
        setattr(pi_synthesis_module, name, value)
    for name, value in snapshot.get("pi_synthesis_lock_path", {}).items():
        if value is not None:
            setattr(pi_synthesis_module, name, value)


@contextmanager
def temporary_workspace_binding(
    workspace_root: Path,
    *,
    sessions_dir: Path | None = None,
    harness_paths: Path | None = None,
    pi_agent_dir: Path | None = None,
    disable_self_observation: bool = True,
    synthesis_lock_path_factory: Any | None = None,
    stop_runtime_on_enter: bool = True,
    stop_runtime_on_exit: bool = True,
):
    """Temporarily bind Syke runtime modules to a replay/eval workspace.

    Replay and benchmark code rely on workspace-module globals plus a few env
    vars for containment. Centralize that mutation here so ask/judge paths do
    not each hand-roll the same setup/teardown logic.
    """
    from syke.llm.backends import pi_synthesis as pi_synthesis_module
    from syke.runtime import stop_pi_runtime
    from syke.runtime import workspace as workspace_module

    snapshot = capture_workspace_bindings()
    old_self_obs = os.environ.get("SYKE_DISABLE_SELF_OBSERVATION")
    old_harness_paths = os.environ.get("SYKE_SANDBOX_HARNESS_PATHS")
    old_pi_agent_dir = os.environ.get("SYKE_PI_AGENT_DIR")

    if stop_runtime_on_enter:
        stop_pi_runtime()

    workspace_module.set_workspace_root(workspace_root)
    for name in _WORKSPACE_GLOBALS:
        setattr(pi_synthesis_module, name, getattr(workspace_module, name))

    if sessions_dir is not None:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        workspace_module.SESSIONS_DIR = sessions_dir
        pi_synthesis_module.SESSIONS_DIR = sessions_dir

    if synthesis_lock_path_factory is not None:
        pi_synthesis_module._synthesis_lock_path = synthesis_lock_path_factory

    if disable_self_observation:
        os.environ["SYKE_DISABLE_SELF_OBSERVATION"] = "1"

    if harness_paths is not None:
        os.environ["SYKE_SANDBOX_HARNESS_PATHS"] = str(harness_paths)

    if pi_agent_dir is not None:
        os.environ["SYKE_PI_AGENT_DIR"] = str(pi_agent_dir)

    try:
        yield
    finally:
        if stop_runtime_on_exit:
            try:
                stop_pi_runtime()
            except Exception:
                log.warning(
                    "Workspace binding cleanup: failed to stop Pi runtime cleanly",
                    exc_info=True,
                )
        restore_workspace_bindings(snapshot)
        if old_self_obs is None:
            os.environ.pop("SYKE_DISABLE_SELF_OBSERVATION", None)
        else:
            os.environ["SYKE_DISABLE_SELF_OBSERVATION"] = old_self_obs
        if old_harness_paths is None:
            os.environ.pop("SYKE_SANDBOX_HARNESS_PATHS", None)
        else:
            os.environ["SYKE_SANDBOX_HARNESS_PATHS"] = old_harness_paths
        if old_pi_agent_dir is None:
            os.environ.pop("SYKE_PI_AGENT_DIR", None)
        else:
            os.environ["SYKE_PI_AGENT_DIR"] = old_pi_agent_dir


def _configure_replay_provider(
    workspace_root: Path,
    provider: str | None,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> None:
    """Write provider credentials into the replay workspace's .pi/ config.

    This routes Pi to a custom endpoint (e.g., Azure Foundry) without
    touching the live ~/.syke/pi-agent/ install.
    """
    pi_dir = workspace_root / ".pi"
    pi_dir.mkdir(parents=True, exist_ok=True)

    # auth.json — provider credential
    auth_path = pi_dir / "auth.json"
    auth: dict[str, object] = {}
    if auth_path.exists():
        auth = json.loads(auth_path.read_text(encoding="utf-8"))
    effective_provider = provider or "openai"
    if api_key:
        auth[effective_provider] = {"type": "api-key", "key": api_key}
    auth_path.write_text(json.dumps(auth, indent=2), encoding="utf-8")

    # settings.json — provider + model + baseUrl
    settings_path = pi_dir / "settings.json"
    settings: dict[str, object] = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["defaultProvider"] = effective_provider
    if model:
        settings["defaultModel"] = model
    if base_url:
        settings["model"] = {"baseUrl": base_url}
    settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def configure_bundle_workspace(output_dir: Path, bundle_path: Path) -> tuple[Path, Path]:
    """Set up replay workspace using a materialized bundle's raw harness files."""
    import shutil

    from syke.llm.backends import pi_synthesis as pi_synthesis_module
    from syke.pi_state import get_pi_agent_dir
    from syke.runtime import stop_pi_runtime
    from syke.runtime import workspace as workspace_module
    from syke.runtime.psyche_md import write_psyche_md

    # Workspace lives under ~/.syke-lab/ — outside ~/Documents so the
    # deny-default sandbox allows it. No AGENTS.md traversal issues.
    run_name = output_dir.name
    workspace_root = Path.home() / ".syke-lab" / run_name / "workspace"
    stop_pi_runtime()
    workspace_module.set_workspace_root(workspace_root)
    # Rebind pi_synthesis module globals to the replay workspace.
    # Still needed because _read_memex_artifact / _write_memex_artifact
    # read from module-level WORKSPACE_ROOT. Will be eliminated when
    # pi_synthesize is fully parameterized.
    for name in _WORKSPACE_GLOBALS:
        setattr(pi_synthesis_module, name, getattr(workspace_module, name))
    pi_synthesis_module._synthesis_lock_path = lambda user_id, workspace_root=workspace_root: (
        workspace_root / ".locks" / f"{user_id}.synthesis.lock"
    )

    # Create dirs
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "sessions").mkdir(exist_ok=True)

    # Seed workspace-local Pi state from the active Syke Pi state so replay
    # inherits the live auth/provider/model by default while still remaining
    # isolated from subsequent mutations.
    workspace_pi_dir = workspace_root / ".pi"
    workspace_pi_dir.mkdir(parents=True, exist_ok=True)
    source_pi_dir = get_pi_agent_dir()
    for name in ("auth.json", "settings.json", "models.json"):
        source_path = source_pi_dir / name
        target_path = workspace_pi_dir / name
        if source_path.exists() and not target_path.exists():
            shutil.copy2(source_path, target_path)

    # Install bundle adapters (NOT shipped seeds) — these will be overwritten
    # per-cycle by rewire_adapters_to_slice() so they point at the truncated
    # slice instead of the full bundle. Initial install is for a clean PSYCHE
    # write on workspace setup.
    adapters_dir = workspace_root / "adapters"
    if adapters_dir.exists():
        shutil.rmtree(adapters_dir)
    adapters_dir.mkdir(parents=True, exist_ok=True)
    bundle_adapters = bundle_path / "adapters"
    if bundle_adapters.exists():
        for adapter in bundle_adapters.glob("*.md"):
            shutil.copy2(adapter, adapters_dir / adapter.name)

    # Write PSYCHE.md from installed adapters — pass workspace_root as home
    # so discovered_roots() doesn't fall through to the live ~/.codex.
    write_psyche_md(workspace_root, home=workspace_root)

    return workspace_root, workspace_module.SYKE_DB


def rewire_adapters_to_slice(workspace_root: Path, slice_dir: Path) -> None:
    """Copy slice-specific adapters into the workspace, then refresh PSYCHE.

    Called before each synthesis cycle so the agent reads adapter files
    whose paths reference the time-truncated slice, not the full bundle.
    """
    import shutil

    slice_adapters = slice_dir / "adapters"
    if not slice_adapters.exists():
        return
    ws_adapters = workspace_root / "adapters"
    if ws_adapters.exists():
        shutil.rmtree(ws_adapters)
    ws_adapters.mkdir(parents=True, exist_ok=True)
    for adapter in slice_adapters.glob("*.md"):
        shutil.copy2(adapter, ws_adapters / adapter.name)

    # Regenerate PSYCHE so the path listing in its body matches the slice too.
    from syke.runtime.psyche_md import write_psyche_md

    write_psyche_md(workspace_root, home=slice_dir)


# Live-path substrings that must NOT appear in replay bash commands. If
# any do, the cycle's memex is considered contaminated and the judge must
# exclude it from scoring. Soft containment — the OS sandbox would be the
# hard version, but it hangs Pi on this host.
_LIVE_PATH_MARKERS = (
    "/Users/saxenauts/.codex",
    "/Users/saxenauts/.claude",
    "/Users/saxenauts/.local/share/opencode",
    "/Users/saxenauts/.local/share/claude",
    "/Users/saxenauts/Documents/personal",
    "/Users/saxenauts/Documents/InnerNets",
    "~/.codex",
    "~/.claude",
    "~/Documents",
)


def _detect_sandbox_escape(
    snapshot: dict[str, Any],
    workspace_root: Path,
    slice_dir: Path,
) -> dict[str, Any]:
    """Scan cycle transcript for bash commands that hit live user paths.

    Returns a dict with `escaped` (bool) and `paths` (list of offending
    substrings found). Allow-lists the replay workspace and slice dir —
    paths under those are legitimate even if they happen to contain
    substrings like "Documents/personal/syke/_internal/...".
    """
    allowed_prefixes = (
        str(workspace_root.resolve()),
        str(slice_dir.resolve()),
    )

    hits: list[str] = []
    tool_calls = snapshot.get("tool_calls_detail") or []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = (call.get("name") or call.get("tool_name") or "").lower()
        if "bash" not in name:
            continue
        args = call.get("arguments") or call.get("args") or {}
        if isinstance(args, str):
            cmd = args
        elif isinstance(args, dict):
            cmd = args.get("command") or args.get("cmd") or ""
        else:
            cmd = ""
        if not isinstance(cmd, str) or not cmd:
            continue
        # Strip allow-listed absolute paths before pattern-matching.
        scrub = cmd
        for prefix in allowed_prefixes:
            scrub = scrub.replace(prefix, "")
        for marker in _LIVE_PATH_MARKERS:
            if marker in scrub:
                hits.append(marker)
                break

    return {"escaped": bool(hits), "paths": sorted(set(hits))}


def _write_manifest_json(runs_dir: Path) -> None:
    """Scan `runs_dir` for sibling runs with replay_results.json and write manifest.

    The viz (`replay_viz.html`) reads `./runs/manifest.json` to populate the
    run-switcher dropdown. Schema: a flat array of
    `{name, days, cost, results_path}` entries. We regenerate it whenever a
    new run completes.
    """
    if not runs_dir.exists():
        return
    entries = []
    # Recurse: a run is any directory containing replay_results.json
    # (group dirs like `ablation_20d/` can hold several nested runs).
    for results in sorted(runs_dir.rglob("replay_results.json")):
        child = results.parent
        rel_name = "/".join(child.relative_to(runs_dir).parts)
        rel_path = "/".join(results.relative_to(runs_dir).parts)
        days = 0
        cost = 0.0
        try:
            data = json.loads(results.read_text(encoding="utf-8"))
            meta = data.get("metadata") or {}
            days = int(
                meta.get("total_days")
                or meta.get("completed_cycles")
                or len(data.get("timeline") or [])
            )
            cost = float(meta.get("total_cost_usd") or 0.0)
        except Exception:
            # Corrupt sibling shouldn't tank the manifest.
            continue
        entries.append(
            {
                "name": rel_name,
                "days": days,
                "cost": cost,
                "results_path": f"./runs/{rel_path}",
                "started_at": meta.get("started_at", ""),
            }
        )
    # Newest-first by started_at timestamp so the viz auto-selects the latest run.
    entries.sort(key=lambda e: e.get("started_at", ""), reverse=True)
    manifest_path = runs_dir / "manifest.json"
    manifest_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def run_bundle_replay(
    bundle_path: Path,
    output_dir: Path,
    user_id: str,
    dry_run: bool,
    max_days: int | None,
    start_day: str | None,
    condition: str,
    skill_file: Path | None = None,
    model: str | None = None,
    cycles_per_day: int = 1,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Run replay using a materialized bundle — agent reads raw harness files."""
    import json as _json

    bundle_meta_path = bundle_path / "meta.json"
    if not bundle_meta_path.exists():
        raise FileNotFoundError(f"Not a bundle: {bundle_path} (no meta.json)")

    bundle_meta = _json.loads(bundle_meta_path.read_text())
    window_start = bundle_meta["window_start"]
    window_end = bundle_meta["window_end"]

    # Use a neutral internal user_id
    external_user_id = user_id
    user_id = "user"
    invoked_at = datetime.now(UTC)

    # Build day list from window range
    from datetime import timedelta

    def _parse_window(s: str) -> datetime:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.strptime(s, "%Y-%m-%d")

    start_dt = _parse_window(start_day) if start_day else _parse_window(window_start)
    end_dt = _parse_window(window_end)
    all_days = []
    current = start_dt
    while current <= end_dt:
        all_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    days = list(all_days)
    if max_days:
        days = days[:max_days]

    # Build units (1 or more cycles per day)
    units = []
    for day in days:
        for c in range(cycles_per_day):
            label = day if cycles_per_day == 1 else f"{day} [{c + 1}/{cycles_per_day}]"
            units.append(
                {"key": label, "day": day, "bucket_index": c + 1, "bucket_count": cycles_per_day}
            )

    if dry_run:
        print(f"Bundle: {bundle_meta.get('tag', bundle_path.name)}")
        print(f"Window: {window_start} to {window_end}")
        print(f"Selected: {len(days)} days, {len(units)} cycles")
        print(f"Sources: {', '.join(bundle_meta.get('sources', {}).keys())}")
        for i, unit in enumerate(units, 1):
            print(f"  Cycle {i}: {unit['key']}")
        return {"dry_run": True, "total_days": len(days), "total_cycles": len(units)}

    replay_binding_snapshot = capture_workspace_bindings()
    os.environ["SYKE_DISABLE_SELF_OBSERVATION"] = "1"
    # OS sandbox: ENABLED. Replay must read only the current frozen slice plus
    # its workspace-local Pi state, never the live harness catalog or global
    # ~/.syke state.
    os.environ.pop("SYKE_DISABLE_SANDBOX", None)
    # Empty-memex tolerance: ablation conditions like `zero` may legitimately
    # produce no memex content. Without this, pi_synthesis rolls back the
    # transaction and labels the cycle failed, even though the cycle ran and
    # consumed budget.
    os.environ["SYKE_ALLOW_EMPTY_MEMEX"] = "1"

    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root, replay_db_path = configure_bundle_workspace(output_dir, bundle_path)
    old_harness_paths = os.environ.get("SYKE_SANDBOX_HARNESS_PATHS")
    old_pi_agent_dir = os.environ.get("SYKE_PI_AGENT_DIR")
    os.environ["SYKE_PI_AGENT_DIR"] = str(workspace_root / ".pi")
    log.info("Bundle replay workspace: %s", workspace_root)

    # Provider override: write credentials into workspace .pi/ config
    # and point SYKE_PI_AGENT_DIR at it so Pi reads from there, not ~/.syke/pi-agent/
    if provider or api_key or base_url:
        _configure_replay_provider(workspace_root, provider, api_key, base_url, model)
        if api_key:
            # Separate auth: point Pi at workspace .pi/ for credentials
            os.environ["SYKE_PI_AGENT_DIR"] = str(workspace_root / ".pi")
        # If only base_url: keep live auth but route via model.baseUrl in workspace settings
        log.info(
            "Replay provider: %s (base_url override: %s)", provider or "default", bool(base_url)
        )

    # Skill wiring: the agent must see EXACTLY what production sees —
    # PSYCHE + MEMEX + skill. No replay awareness, no containment
    # directives. The time sandbox (cycle_slicer) IS the containment:
    # future data physically doesn't exist in the slice.
    # Synthesis path: for non-production conditions, write the condition's
    # synthesis text to a file and pass it via synthesis_path param to
    # pi_synthesize → build_prompt. No monkey-patching SYNTHESIS_PATH.
    from syke.runtime.psyche_md import SYNTHESIS_PATH

    effective_condition = f"custom:{skill_file.name}" if skill_file else condition
    replay_skill_path: Path | None = None  # None = use default SYNTHESIS_PATH

    if skill_file:
        replay_skill_path = skill_file.resolve()
    elif condition != "production":
        condition_skill_text = build_skill_override(condition)
        if condition_skill_text is not None:
            replay_skill_path = workspace_root / ".replay_skill.md"
            replay_skill_path.write_text(condition_skill_text, encoding="utf-8")

    try:
        _sp = replay_skill_path or SYNTHESIS_PATH
        skill_text = _sp.read_text(encoding="utf-8") if _sp.exists() else ""
        skill_hash = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    except FileNotFoundError:
        skill_text, skill_hash = "", ""

    # Clean start
    _unlink_if_present(replay_db_path)
    _unlink_if_present(workspace_root / "MEMEX.md")

    replay_db = SykeDB(replay_db_path)
    timeline: list[dict[str, Any]] = []
    cumulative_cost = 0.0

    result_metadata = {
        "mode": "bundle",
        "bundle_path": str(bundle_path),
        "bundle_tag": bundle_meta.get("tag"),
        "syke_db": str(replay_db_path),
        "user_id": external_user_id,
        "internal_user_id": user_id,
        "condition": effective_condition,
        "window_start": window_start,
        "window_end": window_end,
        "selected_start_day": days[0] if days else None,
        "selected_end_day": days[-1] if days else None,
        "selected_observed_days": len(days),
        "selected_replay_cycles": len(units),
        "cycles_per_day": cycles_per_day,
        "started_at": invoked_at.isoformat(),
        "completed_at": None,
        "status": "running",
        "partial": True,
        "completed_cycles": 0,
        "total_days": len(days),
        "total_cost_usd": 0.0,
        "skill_content": skill_text,
        "skill_hash": skill_hash,
        "error": None,
    }
    result_data = {"metadata": result_metadata, "timeline": timeline}

    # Per-cycle slice workspace: slices live under the run's output_dir so
    # they travel with the run and get cleaned up with it. Each slice is a
    # small bundle containing only the harness data visible at end-of-day N.
    # Slices also live under ~/.syke-lab/ so adapter paths in the
    # slice point to sandbox-allowed locations (not ~/Documents).
    slice_root = Path.home() / ".syke-lab" / output_dir.name / "cycle_slices"
    slice_root.mkdir(parents=True, exist_ok=True)
    # Retain every slice for post-hoc judge audits. Each slice is small
    # (APFS clonefile + the filtered opencode DB), so the real cost is
    # disk pressure on long runs — acceptable during experiments.
    _SLICE_RETENTION = 10**6

    try:
        _persist_run_checkpoint(output_dir, result_data)

        for i, unit in enumerate(units, 1):
            day = str(unit["day"])
            cycle_key = str(unit["key"])

            # Simulated time: end of this day. Naive (no tzinfo) because
            # pi_synthesis treats now_override as local time and subtracts
            # it from stripped-aware cycle_records timestamps.
            simulated_now = datetime.strptime(day, "%Y-%m-%d").replace(hour=23, minute=59)

            # Slice the bundle to the cycle boundary — files physically
            # disappear past this timestamp so the agent cannot read future
            # data. Hard containment at the filesystem level.
            cycle_slice_dir = slice_root / f"cycle_{i:04d}"
            slice_bundle(bundle_path, simulated_now, cycle_slice_dir)

            # Rewire workspace adapters to point at this slice. PSYCHE is
            # regenerated with home=slice so its path listing matches.
            rewire_adapters_to_slice(workspace_root, cycle_slice_dir)
            os.environ["SYKE_SANDBOX_HARNESS_PATHS"] = str(cycle_slice_dir)

            _clear_stale_synthesis_lock(user_id)

            result = synthesize(
                replay_db,
                user_id,
                workspace_root=workspace_root,
                home=workspace_root,
                skill_path=replay_skill_path,
                model_override=model,
                now_override=simulated_now,
            )

            # Retention: keep only the last N slices. Earlier ones are
            # reconstructible from the bundle + cycle timestamp, so deletion
            # is safe and avoids disk bloat (each slice is ~600 MB).
            for old_idx in range(1, i - _SLICE_RETENTION + 1):
                old_slice = slice_root / f"cycle_{old_idx:04d}"
                if old_slice.exists():
                    import shutil as _sh

                    _sh.rmtree(old_slice, ignore_errors=True)

            # Snapshot
            snapshot = snapshot_memex(
                replay_db,
                user_id,
                day,
                i,
                result,
                cycle_cutoff_iso=simulated_now.isoformat(),
            )
            snapshot["day"] = cycle_key
            snapshot["bucket_index"] = int(unit["bucket_index"])
            snapshot["bucket_count"] = int(unit["bucket_count"])

            # Audit: scan this cycle's bash commands for live-path escapes
            # outside the replay workspace + slice. The OS sandbox hangs Pi
            # on this host, so we do soft containment + transcript audit.
            escape_report = _detect_sandbox_escape(snapshot, workspace_root, cycle_slice_dir)
            snapshot["sandbox_escape"] = escape_report["escaped"]
            snapshot["escape_paths"] = escape_report["paths"]
            if escape_report["escaped"]:
                log.warning(
                    "Cycle %d escaped to %d live paths: %s",
                    i,
                    len(escape_report["paths"]),
                    escape_report["paths"][:3],
                )

            cost_val = result.get("cost_usd", 0)
            cost = float(cost_val) if isinstance(cost_val, (int, float, str)) else 0.0
            cumulative_cost += cost
            result_metadata["total_cost_usd"] = cumulative_cost
            result_metadata["completed_cycles"] = i
            result_metadata["last_completed_day"] = day
            result_metadata["partial"] = i < len(units)

            timeline.append(snapshot)
            _persist_run_checkpoint(output_dir, result_data)

            print(
                f"Cycle {i}/{len(units)} | {cycle_key} | "
                f"memex: {snapshot.get('chars', 0):,} chars | "
                f"{snapshot.get('memories_active', 0)} memories | "
                f"${cost:.2f}"
            )

        result_metadata["status"] = "completed"
        result_metadata["completed_at"] = datetime.now(UTC).isoformat()
        _persist_run_checkpoint(output_dir, result_data)

        # Regenerate manifest.json so the viz dropdown picks up this run.
        _write_manifest_json(output_dir.parent)

        print(f"\nResults written to: {output_dir / 'replay_results.json'}")
        print(f"Total cost: ${cumulative_cost:.2f}")
        return result_data

    except Exception as exc:
        result_metadata["status"] = "failed"
        result_metadata["error"] = str(exc)
        _persist_run_checkpoint(output_dir, result_data)
        raise
    finally:
        from syke.runtime import stop_pi_runtime

        try:
            stop_pi_runtime()
        except Exception:
            log.warning("Replay cleanup: failed to stop Pi runtime cleanly", exc_info=True)
        if old_harness_paths is None:
            os.environ.pop("SYKE_SANDBOX_HARNESS_PATHS", None)
        else:
            os.environ["SYKE_SANDBOX_HARNESS_PATHS"] = old_harness_paths
        if old_pi_agent_dir is None:
            os.environ.pop("SYKE_PI_AGENT_DIR", None)
        else:
            os.environ["SYKE_PI_AGENT_DIR"] = old_pi_agent_dir
        restore_workspace_bindings(replay_binding_snapshot)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()

    output_dir = Path(args.output_dir).resolve()

    skill_file = Path(args.skill).resolve() if args.skill else None
    if skill_file and not skill_file.exists():
        raise SystemExit(f"Skill file not found: {skill_file}")

    bundle_path = Path(args.bundle).resolve()
    if not (bundle_path / "meta.json").exists():
        raise SystemExit(f"Not a bundle (no meta.json): {bundle_path}")

    run_bundle_replay(
        bundle_path=bundle_path,
        output_dir=output_dir,
        user_id=args.user_id,
        dry_run=args.dry_run,
        max_days=args.max_days,
        start_day=args.start_day,
        condition=args.condition,
        skill_file=skill_file,
        model=args.model,
        cycles_per_day=args.cycles_per_day,
        provider=args.provider,
        api_key=args.api_key,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    main()
