"""
Pi-based agentic synthesis.

Uses the persistent Pi runtime to run synthesis cycles.
The agent operates in the workspace with full tool access:
- reads events.db (immutable timeline)
- writes syke.db (canonical mutable workspace database)
- updates MEMEX.md (routed workspace artifact)
- builds scripts in scripts/ (persistent analysis tools)

This replaces the old spawn-per-cycle PiClient approach with a
persistent runtime managed by the Syke daemon.
"""

from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

from uuid_extensions import uuid7

from syke.config import (
    CFG,
    SETUP_SYNC_MAX_TURNS,
    SYNC_MAX_TURNS,
    user_data_dir,
    user_events_db_path,
)
from syke.db import SykeDB
from syke.llm.pi_client import resolve_pi_model
from syke.runtime.workspace import (
    EVENTS_DB,
    MEMEX_PATH,
    SESSIONS_DIR,
    SYKE_DB,
    WORKSPACE_ROOT,
    get_pending_event_count,
    prepare_workspace,
    validate_workspace,
)

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows platforms
    msvcrt = None

# ── Skill prompt loading ──────────────────────────────────────────────

SKILL_PATH = Path(__file__).parent / "skills" / "pi_synthesis.md"


class SynthesisLockUnavailable(RuntimeError):
    """Raised when another synthesis cycle already holds the user lock."""


def _synthesis_lock_path(user_id: str) -> Path:
    return user_data_dir(user_id) / "synthesis.lock"


def _acquire_synthesis_lock(user_id: str) -> tuple[TextIO, Path]:
    lock_path = _synthesis_lock_path(user_id)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SynthesisLockUnavailable(str(lock_path)) from exc
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            try:
                if lock_path.stat().st_size == 0:
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SynthesisLockUnavailable(str(lock_path)) from exc
        else:  # pragma: no cover - unsupported platform
            logger.warning(
                "No synthesis lock backend available; continuing without cross-process guard"
            )
            return handle, lock_path

        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\t{datetime.now(UTC).isoformat()}\n")
        handle.flush()
        return handle, lock_path
    except Exception:
        handle.close()
        raise


def _release_synthesis_lock(handle: TextIO) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows fallback
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def _load_skill_prompt() -> str:
    """Load the synthesis skill prompt as static text."""
    if not SKILL_PATH.exists():
        raise FileNotFoundError(f"Skill prompt not found: {SKILL_PATH}")
    return SKILL_PATH.read_text()


# ── Post-cycle validation ────────────────────────────────────────────


def _validate_cycle_output() -> dict[str, object]:
    """
    Validate what the agent produced during the cycle.

    Checks:
    - syke.db exists and is readable
    - No corruption detected
    """
    issues: list[str] = []
    stats: dict[str, object] = {}

    if MEMEX_PATH.exists():
        content = MEMEX_PATH.read_text(encoding="utf-8").strip()
        stats["memex_artifact_exists"] = True
        stats["memex_artifact_size"] = len(content)
        stats["memex_artifact_empty"] = not bool(content)
    else:
        stats["memex_artifact_exists"] = False

    # Check syke.db
    if SYKE_DB.exists() and SYKE_DB.stat().st_size > 0:
        try:
            conn = sqlite3.connect(f"file:{SYKE_DB}?mode=ro", uri=True)
            try:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                stats["memory_tables"] = [t[0] for t in tables]

                for t in tables:
                    if t[0] == "memories":
                        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                        stats["memory_count"] = count
                        break
            finally:
                conn.close()
        except sqlite3.Error as e:
            issues.append(f"syke.db read error: {e}")
    else:
        stats["syke_db_empty"] = True

    # Check events.db wasn't tampered with
    if EVENTS_DB.exists():
        import os

        if os.access(EVENTS_DB, os.W_OK):
            issues.append("events.db is writable (security violation)")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "stats": stats,
    }


# ── Memex authority: canonical DB + routed workspace artifact ───────


def _current_memex_content(db: SykeDB, user_id: str) -> str | None:
    memex = db.get_memex(user_id)
    if not memex:
        return None
    content = memex.get("content")
    return content if isinstance(content, str) and content.strip() else None


def _read_memex_artifact() -> str | None:
    if not MEMEX_PATH.exists():
        return None
    content = MEMEX_PATH.read_text(encoding="utf-8").strip()
    return content or None


def _write_memex_artifact(content: str) -> bool:
    existing = _read_memex_artifact()
    if existing == content:
        return False
    MEMEX_PATH.write_text(content + "\n", encoding="utf-8")
    return True


def _resolve_source_db_path(db: SykeDB, user_id: str) -> Path:
    event_db_path = getattr(db, "event_db_path", None)
    if isinstance(event_db_path, str | Path):
        return Path(event_db_path)

    db_path = getattr(db, "db_path", None)
    if isinstance(db_path, str | Path):
        candidate = Path(db_path)
        if candidate.name != "syke.db":
            return candidate

    return user_events_db_path(user_id)


def _sync_memex_to_db(
    db: SykeDB,
    user_id: str,
    *,
    previous_content: str | None = None,
    previous_artifact_content: str | None = None,
) -> dict[str, object]:
    """
    Resolve canonical memex state in syke.db and project it back to MEMEX.md.

    MEMEX.md is an artifact surface. If the cycle did not directly change the
    canonical memex in syke.db, a changed non-empty MEMEX.md can still be
    imported as cycle output for compatibility with the current Pi prompt
    contract.
    """
    result: dict[str, object] = {
        "ok": False,
        "updated": False,
        "source": "missing",
        "artifact_written": False,
    }

    from syke.memory.memex import update_memex

    current_content = _current_memex_content(db, user_id)
    artifact_content = _read_memex_artifact()
    db_changed_during_cycle = current_content != previous_content
    artifact_changed_during_cycle = artifact_content != previous_artifact_content

    if db_changed_during_cycle and current_content is not None:
        canonical_content = current_content
        result["source"] = "db"
    elif artifact_content is not None and artifact_changed_during_cycle:
        canonical_content = artifact_content
        result["source"] = "artifact"
        try:
            update_memex(db, user_id, canonical_content)
            logger.info(
                "Memex artifact synced into canonical DB (%d chars)", len(canonical_content)
            )
        except Exception as e:
            logger.error(f"Failed to sync memex artifact into DB: {e}")
            return result
        current_content = _current_memex_content(db, user_id)
        if current_content is None:
            logger.error("Memex artifact sync completed but canonical memex is still missing")
            return result
        canonical_content = current_content
    elif current_content is not None:
        canonical_content = current_content
        result["source"] = "db"
    else:
        logger.error("No canonical memex available after synthesis")
        return result

    try:
        result["artifact_written"] = _write_memex_artifact(canonical_content)
        result["updated"] = canonical_content != previous_content
        result["ok"] = True
        logger.info(
            "Canonical memex ready (%d chars, source=%s)",
            len(canonical_content),
            result["source"],
        )
        return result
    except Exception as e:
        logger.error(f"Failed to project canonical memex artifact: {e}")
        return result


def _summarize_tools(tool_calls: list[dict[str, object]]) -> tuple[list[str], dict[str, int]]:
    names: list[str] = []
    counts: dict[str, int] = {}
    for tool_call in tool_calls:
        name = tool_call.get("name") or tool_call.get("tool") or "tool"
        name_str = str(name)
        names.append(name_str)
        counts[name_str] = counts.get(name_str, 0) + 1
    return names, counts


def _normalize_pi_tool_name(name: str) -> str:
    lowered = name.strip().lower()
    if lowered == "bash":
        return "Bash"
    if lowered == "read":
        return "Read"
    if lowered == "write":
        return "Write"
    if lowered == "edit":
        return "Edit"
    return name


def _assistant_block_to_transcript_block(block: object) -> dict[str, object] | None:
    if not isinstance(block, dict):
        return None

    block_type = block.get("type")
    if block_type == "thinking":
        thinking = block.get("thinking")
        if isinstance(thinking, str):
            return {"type": "thinking", "text": thinking}
        return {"type": "thinking", "text": ""}

    if block_type == "text":
        text = block.get("text")
        if isinstance(text, str):
            return {"type": "text", "text": text}
        return None

    if block_type == "toolCall":
        name = block.get("name")
        arguments = block.get("arguments")
        return {
            "type": "tool_use",
            "name": _normalize_pi_tool_name(str(name or "tool")),
            "input": arguments if isinstance(arguments, dict) else {},
        }

    return None


def _serialize_pi_transcript(events: list[dict[str, object]]) -> list[dict[str, object]]:
    transcript: list[dict[str, object]] = []
    for event in events:
        if event.get("type") != "message":
            continue

        message = event.get("message")
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        if not isinstance(role, str):
            continue

        if role == "assistant":
            content = message.get("content")
            blocks: list[dict[str, object]] = []
            if isinstance(content, list):
                for block in content:
                    transcript_block = _assistant_block_to_transcript_block(block)
                    if transcript_block is not None:
                        blocks.append(transcript_block)
            transcript.append({"role": role, "blocks": blocks})
            continue

        if role == "user":
            content = message.get("content")
            text_parts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
            transcript.append(
                {"role": role, "blocks": [{"type": "text", "text": "".join(text_parts)}]}
            )
            continue

        if role == "toolResult":
            text_parts: list[str] = []
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            text_parts.append(text)
            transcript.append(
                {
                    "role": role,
                    "blocks": [
                        {
                            "type": "tool_result",
                            "name": _normalize_pi_tool_name(str(message.get("toolName") or "tool")),
                            "text": "".join(text_parts),
                            "is_error": bool(message.get("isError", False)),
                        }
                    ],
                }
            )

    return transcript


def _count_pi_turns(transcript: list[dict[str, object]]) -> int:
    return sum(1 for turn in transcript if turn.get("role") == "assistant")


def _safe_runtime_status(runtime: object) -> dict[str, object]:
    status_fn = getattr(runtime, "status", None)
    if callable(status_fn):
        try:
            status = status_fn()
            if isinstance(status, dict):
                return status
        except Exception:
            logger.debug("Failed to read Pi runtime status", exc_info=True)
    return {}


def _record_pi_tool_observations(
    observer: object,
    run_id: str,
    tool_calls: list[dict[str, object]],
) -> None:
    from syke.observe.trace import SYNTHESIS_TOOL_USE

    if observer is None:
        return

    for index, tool_call in enumerate(tool_calls, start=1):
        try:
            tool_name = tool_call.get("name") or tool_call.get("tool") or "tool"
            tool_input = tool_call.get("input")
            observer.record(
                SYNTHESIS_TOOL_USE,
                {
                    "tool_name": str(tool_name),
                    "tool_input": tool_input,
                    "tool_index": index,
                    "success": True,
                },
                run_id=run_id,
            )
        except Exception:
            logger.debug("Failed to record Pi synthesis tool observation", exc_info=True)


def _record_pi_metrics(
    user_id: str,
    *,
    operation: str,
    duration_ms: int,
    cost_usd: float | None,
    input_tokens: int | None,
    output_tokens: int | None,
    num_turns: int = 0,
    events_processed: int = 0,
    details: dict[str, object] | None = None,
) -> None:
    try:
        from syke.metrics import MetricsTracker, RunMetrics

        tracker = MetricsTracker(user_id)
        completed_at = datetime.now(UTC)
        started_at = completed_at - timedelta(milliseconds=max(duration_ms, 0))
        tracker.record(
            RunMetrics(
                operation=operation,
                user_id=user_id,
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                duration_seconds=duration_ms / 1000.0,
                duration_api_ms=duration_ms,
                cost_usd=float(cost_usd or 0.0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                num_turns=max(num_turns, 0),
                events_processed=events_processed,
                details=details or {},
            )
        )
    except Exception:
        logger.debug("Failed to record Pi metrics", exc_info=True)


# ── Main entry point ──────────────────────────────────────────────────


def pi_synthesize(
    db: SykeDB,
    user_id: str,
    *,
    force: bool = False,
    skill_override: str | None = None,
    model_override: str | None = None,
    first_run: bool | None = None,
) -> dict[str, object]:
    """
    Run one Pi synthesis cycle.

    This is the main entry point called by synthesis.py when runtime='pi'.

    Flow:
    1. Setup/validate workspace
    2. Refresh events.db snapshot
    3. Check pending event threshold
    4. Build skill prompt
    5. Send to persistent Pi runtime
    6. Validate output
    7. Sync memex to Syke DB
    8. Record cycle

    Returns dict with cycle results and metrics.
    """
    start_time = time.time()
    result: dict[str, object] = {
        "backend": "pi",
        "status": "pending",
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "events_processed": None,
        "memex_updated": None,
        "num_turns": 0,
        "error": None,
        "reason": None,
    }
    observer_api = importlib.import_module("syke.observe.trace")
    observer = observer_api.SykeObserver(db, user_id)
    run_id = str(uuid7())
    started_at = datetime.now(UTC)
    observer.record(
        observer_api.SYNTHESIS_START,
        {"start_time": started_at.isoformat()},
        run_id=run_id,
    )
    previous_memex_content = _current_memex_content(db, user_id)
    is_first_run = first_run if first_run is not None else previous_memex_content is None
    previous_memex_artifact_content = _read_memex_artifact()

    def _record_completion(final_result: dict[str, object]) -> None:
        ended_at = datetime.now(UTC)
        observer.record(
            observer_api.SYNTHESIS_COMPLETE,
            {
                "start_time": started_at.isoformat(),
                "end_time": ended_at.isoformat(),
                "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                "events_processed": final_result.get("events_processed", 0),
                "cost_usd": final_result.get("cost_usd", 0.0),
                "status": final_result.get("status", "unknown"),
                "error": final_result.get("error"),
                "provider": final_result.get("provider"),
                "model": final_result.get("model"),
                "response_id": final_result.get("response_id"),
                "stop_reason": final_result.get("stop_reason"),
                "tool_calls": final_result.get("tool_calls", 0),
                "num_turns": final_result.get("num_turns", 0),
                "tool_names": final_result.get("tool_names", []),
                "tool_name_counts": final_result.get("tool_name_counts", {}),
                "cache_read_tokens": final_result.get("cache_read_tokens", 0),
                "cache_write_tokens": final_result.get("cache_write_tokens", 0),
                "runtime_reused": final_result.get("runtime_reused"),
                "runtime_pid": final_result.get("runtime_pid"),
                "runtime_uptime_s": final_result.get("runtime_uptime_s"),
                "runtime_session_count": final_result.get("runtime_session_count"),
                "workspace_refreshed": final_result.get("workspace_refreshed"),
                "workspace_refresh_reason": final_result.get("workspace_refresh_reason"),
                "workspace_refresh_ms": final_result.get("workspace_refresh_ms"),
                "workspace_events_db_size": final_result.get("workspace_events_db_size"),
            },
            run_id=run_id,
        )

    def _run_cycle_locked() -> dict[str, object]:
        # ── 1. Setup workspace ──
        source_db = _resolve_source_db_path(db, user_id)
        syke_db_path = Path(db.db_path) if hasattr(db, "db_path") else None
        workspace_info = prepare_workspace(
            user_id,
            source_db_path=source_db,
            syke_db_path=syke_db_path,
        )
        workspace_refresh = workspace_info.get("refresh", {})
        ws_validation = validate_workspace()
        if not ws_validation["valid"]:
            logger.error(f"Workspace validation failed: {ws_validation['issues']}")
            result["status"] = "failed"
            result["error"] = f"Workspace invalid: {ws_validation['issues']}"
            result["duration_ms"] = int((time.time() - start_time) * 1000)
            result["workspace_refreshed"] = bool(workspace_refresh.get("refreshed", False))
            result["workspace_refresh_reason"] = workspace_refresh.get("reason")
            result["workspace_refresh_ms"] = workspace_refresh.get("duration_ms")
            result["workspace_events_db_size"] = workspace_refresh.get("dest_size_bytes")
            _record_completion(result)
            return result

        # ── 2. Check pending events ──
        pending_count, cursor = get_pending_event_count(user_id)
        threshold = 1
        if CFG and hasattr(CFG, "synthesis") and CFG.synthesis:
            threshold = getattr(CFG.synthesis, "threshold", 1)

        if pending_count < threshold and not force:
            logger.info(f"Below threshold: {pending_count} pending < {threshold} required")
            result["status"] = "skipped"
            result["reason"] = f"Below threshold ({pending_count}/{threshold})"
            result["events_processed"] = pending_count
            result["duration_ms"] = int((time.time() - start_time) * 1000)
            result["memex_updated"] = False
            result["workspace_refreshed"] = bool(workspace_refresh.get("refreshed", False))
            result["workspace_refresh_reason"] = workspace_refresh.get("reason")
            result["workspace_refresh_ms"] = workspace_refresh.get("duration_ms")
            result["workspace_events_db_size"] = workspace_refresh.get("dest_size_bytes")
            ended_at = datetime.now(UTC)
            observer.record(
                observer_api.SYNTHESIS_SKIPPED,
                {
                    "start_time": started_at.isoformat(),
                    "end_time": ended_at.isoformat(),
                    "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                    "events_processed": pending_count,
                    "cost_usd": 0.0,
                    "reason": "below_threshold",
                    "workspace_refreshed": bool(workspace_refresh.get("refreshed", False)),
                    "workspace_refresh_reason": workspace_refresh.get("reason"),
                    "workspace_refresh_ms": workspace_refresh.get("duration_ms"),
                },
                run_id=run_id,
            )
            return result

        logger.info(f"Starting Pi synthesis: {pending_count} pending events")

        # ── 3. Build skill prompt ──
        if skill_override is not None:
            prompt = skill_override
        else:
            prompt = _load_skill_prompt()

        # ── 4. Record cycle start ──
        cycle_id = None
        try:
            cycle_id = db.insert_cycle_record(
                user_id=user_id,
                cursor_start=cursor,
                skill_hash="pi_synthesis",
                prompt_hash=str(hash(prompt))[:16],
                model=model_override or "pi",
            )
        except Exception as e:
            logger.warning(f"Failed to record cycle start: {e}")

        # ── 5. Send to Pi runtime ──
        timeout = 300  # 5 minutes default
        if CFG and hasattr(CFG, "synthesis") and CFG.synthesis:
            timeout = getattr(CFG.synthesis, "timeout", 300)
        # Pi does not expose a hard per-run turn cap, so first-run "more room"
        # is implemented as a proportional timeout increase instead.
        if is_first_run and SYNC_MAX_TURNS > 0 and SETUP_SYNC_MAX_TURNS > SYNC_MAX_TURNS:
            timeout = max(timeout, int(timeout * (SETUP_SYNC_MAX_TURNS / SYNC_MAX_TURNS)))

        runtime_reused = False
        requested_model = resolve_pi_model(model_override)
        try:
            from syke.runtime import get_pi_runtime, start_pi_runtime

            try:
                existing_runtime = get_pi_runtime()
                existing_status = _safe_runtime_status(existing_runtime)
                runtime_reused = (
                    existing_runtime.is_alive
                    and existing_runtime.model == requested_model
                    and existing_status.get("workspace") == str(WORKSPACE_ROOT)
                )
            except RuntimeError:
                runtime_reused = False

            runtime = start_pi_runtime(
                workspace_dir=WORKSPACE_ROOT,
                session_dir=SESSIONS_DIR,
                model=model_override,
            )
            pi_result = runtime.prompt(prompt, timeout=timeout, new_session=True)
        except Exception as e:
            logger.exception("Pi runtime failed during synthesis cycle")
            failure_duration = int((time.time() - start_time) * 1000)
            result["status"] = "failed"
            result["error"] = f"Pi runtime failed: {e}"
            result["duration_ms"] = failure_duration
            result["events_processed"] = pending_count
            result["memex_updated"] = False
            result["runtime_reused"] = runtime_reused
            result["workspace_refreshed"] = bool(workspace_refresh.get("refreshed", False))
            result["workspace_refresh_reason"] = workspace_refresh.get("reason")
            result["workspace_refresh_ms"] = workspace_refresh.get("duration_ms")
            result["workspace_events_db_size"] = workspace_refresh.get("dest_size_bytes")
            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id, status="failed", duration_ms=failure_duration
                    )
                except Exception:
                    pass
            _record_completion(result)
            return result
        runtime_status = _safe_runtime_status(runtime)
        tool_names, tool_name_counts = _summarize_tools(pi_result.tool_calls)
        _record_pi_tool_observations(observer, run_id, pi_result.tool_calls)
        transcript = getattr(pi_result, "transcript", None)
        if not isinstance(transcript, list):
            transcript = _serialize_pi_transcript(pi_result.events)
        num_turns = getattr(pi_result, "num_turns", None)
        if not isinstance(num_turns, int):
            num_turns = _count_pi_turns(transcript)

        result["duration_ms"] = pi_result.duration_ms
        result["cost_usd"] = pi_result.cost_usd
        result["input_tokens"] = pi_result.input_tokens
        result["output_tokens"] = pi_result.output_tokens
        result["cache_read_tokens"] = int(pi_result.cache_read_tokens or 0)
        result["cache_write_tokens"] = int(pi_result.cache_write_tokens or 0)
        result["provider"] = pi_result.provider
        result["model"] = pi_result.response_model
        result["response_id"] = pi_result.response_id
        result["stop_reason"] = pi_result.stop_reason
        result["tool_calls"] = len(pi_result.tool_calls)
        result["tool_names"] = tool_names
        result["tool_name_counts"] = tool_name_counts
        result["transcript"] = transcript
        result["num_turns"] = num_turns
        result["runtime_reused"] = runtime_reused
        result["runtime_pid"] = runtime_status.get("pid")
        result["runtime_uptime_s"] = runtime_status.get("uptime_s")
        result["runtime_session_count"] = runtime_status.get("session_count")
        result["workspace_refreshed"] = bool(workspace_refresh.get("refreshed", False))
        result["workspace_refresh_reason"] = workspace_refresh.get("reason")
        result["workspace_refresh_ms"] = workspace_refresh.get("duration_ms")
        result["workspace_events_db_size"] = workspace_refresh.get("dest_size_bytes")
        tool_call_count = len(pi_result.tool_calls)
        if not pi_result.ok:
            logger.error(f"Pi synthesis failed: {pi_result.error}")
            result["status"] = "failed"
            result["error"] = pi_result.error
            result["duration_ms"] = pi_result.duration_ms
            result["events_processed"] = pending_count
            result["memex_updated"] = False

            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id,
                        status="failed",
                        cost_usd=float(pi_result.cost_usd or 0.0),
                        input_tokens=int(pi_result.input_tokens or 0),
                        output_tokens=int(pi_result.output_tokens or 0),
                        cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                        duration_ms=pi_result.duration_ms,
                    )
                except Exception:
                    pass
            _record_pi_metrics(
                user_id,
                operation="synthesis",
                duration_ms=int(pi_result.duration_ms or 0),
                cost_usd=pi_result.cost_usd,
                input_tokens=pi_result.input_tokens,
                output_tokens=pi_result.output_tokens,
                num_turns=num_turns,
                events_processed=pending_count,
                details={
                    "status": "failed",
                    "tool_calls": tool_call_count,
                    "num_turns": num_turns,
                    "tool_names": tool_names,
                    "tool_name_counts": tool_name_counts,
                    "provider": pi_result.provider,
                    "model": pi_result.response_model,
                    "response_id": pi_result.response_id,
                    "stop_reason": pi_result.stop_reason,
                    "runtime_reused": runtime_reused,
                    "runtime_pid": runtime_status.get("pid"),
                    "runtime_uptime_s": runtime_status.get("uptime_s"),
                    "runtime_start_ms": runtime_status.get("last_start_ms"),
                    "runtime_session_count": runtime_status.get("session_count"),
                    "cache_read_tokens": int(pi_result.cache_read_tokens or 0),
                    "cache_write_tokens": int(pi_result.cache_write_tokens or 0),
                    "workspace_refreshed": bool(workspace_refresh.get("refreshed", False)),
                    "workspace_refresh_reason": workspace_refresh.get("reason"),
                    "workspace_refresh_ms": workspace_refresh.get("duration_ms"),
                    "workspace_events_db_size": workspace_refresh.get("dest_size_bytes"),
                },
            )
            _record_completion(result)
            return result

        # ── 7. Validate output ──
        validation = _validate_cycle_output()

        if not validation["valid"]:
            logger.warning(f"Cycle output validation issues: {validation['issues']}")
            # Don't fail hard — first cycles may not produce everything

        # ── 8. Sync memex to Syke DB ──
        memex_sync = _sync_memex_to_db(
            db,
            user_id,
            previous_content=previous_memex_content,
            previous_artifact_content=previous_memex_artifact_content,
        )
        memex_synced = bool(memex_sync.get("ok", False))
        memex_updated = bool(memex_sync.get("updated", False))

        total_duration = int((time.time() - start_time) * 1000)

        if not memex_synced:
            logger.error(
                "Pi synthesis completed but canonical memex is unavailable; "
                "leaving cursor unchanged"
            )
            result["status"] = "failed"
            result["error"] = "Pi synthesis completed but canonical memex is unavailable"
            result["events_processed"] = pending_count
            result["memex_updated"] = False
            result["duration_ms"] = total_duration
            result["cost_usd"] = pi_result.cost_usd
            result["input_tokens"] = pi_result.input_tokens
            result["output_tokens"] = pi_result.output_tokens

            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id,
                        status="failed",
                        events_processed=pending_count,
                        memex_updated=False,
                        cost_usd=float(pi_result.cost_usd or 0.0),
                        input_tokens=int(pi_result.input_tokens or 0),
                        output_tokens=int(pi_result.output_tokens or 0),
                        cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                        duration_ms=total_duration,
                    )
                except Exception as e:
                    logger.warning(f"Failed to complete cycle record: {e}")

            _record_pi_metrics(
                user_id,
                operation="synthesis",
                duration_ms=total_duration,
                cost_usd=pi_result.cost_usd,
                input_tokens=pi_result.input_tokens,
                output_tokens=pi_result.output_tokens,
                num_turns=num_turns,
                events_processed=pending_count,
                details={
                    "status": "failed",
                    "error": result["error"],
                    "tool_calls": tool_call_count,
                    "num_turns": num_turns,
                    "tool_names": tool_names,
                    "tool_name_counts": tool_name_counts,
                    "provider": pi_result.provider,
                    "model": pi_result.response_model,
                    "response_id": pi_result.response_id,
                    "stop_reason": pi_result.stop_reason,
                    "runtime_reused": runtime_reused,
                    "runtime_pid": runtime_status.get("pid"),
                    "runtime_uptime_s": runtime_status.get("uptime_s"),
                    "runtime_start_ms": runtime_status.get("last_start_ms"),
                    "runtime_session_count": runtime_status.get("session_count"),
                    "cache_read_tokens": int(pi_result.cache_read_tokens or 0),
                    "cache_write_tokens": int(pi_result.cache_write_tokens or 0),
                    "workspace_refreshed": bool(workspace_refresh.get("refreshed", False)),
                    "workspace_refresh_reason": workspace_refresh.get("reason"),
                    "workspace_refresh_ms": workspace_refresh.get("duration_ms"),
                    "workspace_events_db_size": workspace_refresh.get("dest_size_bytes"),
                },
            )
            _record_completion(result)
            return result

        # ── 9. Advance cursor ──
        # Read the latest event ID from events.db to set as new cursor
        latest: tuple[str] | None = None
        try:
            conn = sqlite3.connect(f"file:{EVENTS_DB}?mode=ro", uri=True)
            latest = conn.execute(
                "SELECT id FROM events WHERE user_id = ? ORDER BY rowid DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            conn.close()

            if latest:
                db.set_synthesis_cursor(user_id, latest[0])
                logger.info(f"Cursor advanced to {latest[0]}")
        except Exception as e:
            logger.warning(f"Failed to advance cursor: {e}")

        # ── 10. Complete cycle record ──
        if cycle_id:
            try:
                db.complete_cycle_record(
                    cycle_id=cycle_id,
                    status="completed",
                    cursor_end=latest[0] if latest else None,
                    events_processed=pending_count,
                    memex_updated=memex_updated,
                    cost_usd=float(pi_result.cost_usd or 0.0),
                    input_tokens=int(pi_result.input_tokens or 0),
                    output_tokens=int(pi_result.output_tokens or 0),
                    cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                    duration_ms=total_duration,
                )
            except Exception as e:
                logger.warning(f"Failed to complete cycle record: {e}")

        result["status"] = "completed"
        result["events_processed"] = pending_count
        result["memex_updated"] = memex_updated
        result["duration_ms"] = total_duration
        result["cost_usd"] = pi_result.cost_usd
        result["input_tokens"] = pi_result.input_tokens
        result["output_tokens"] = pi_result.output_tokens

        _record_pi_metrics(
            user_id,
            operation="synthesis",
            duration_ms=total_duration,
            cost_usd=pi_result.cost_usd,
            input_tokens=pi_result.input_tokens,
            output_tokens=pi_result.output_tokens,
            num_turns=num_turns,
            events_processed=pending_count,
            details={
                "status": "completed",
                "tool_calls": tool_call_count,
                "num_turns": num_turns,
                "tool_names": tool_names,
                "tool_name_counts": tool_name_counts,
                "provider": pi_result.provider,
                "model": pi_result.response_model,
                "response_id": pi_result.response_id,
                "stop_reason": pi_result.stop_reason,
                "runtime_reused": runtime_reused,
                "runtime_pid": runtime_status.get("pid"),
                "runtime_uptime_s": runtime_status.get("uptime_s"),
                "runtime_start_ms": runtime_status.get("last_start_ms"),
                "runtime_session_count": runtime_status.get("session_count"),
                "cache_read_tokens": int(pi_result.cache_read_tokens or 0),
                "cache_write_tokens": int(pi_result.cache_write_tokens or 0),
                "workspace_refreshed": bool(workspace_refresh.get("refreshed", False)),
                "workspace_refresh_reason": workspace_refresh.get("reason"),
                "workspace_refresh_ms": workspace_refresh.get("duration_ms"),
                "workspace_events_db_size": workspace_refresh.get("dest_size_bytes"),
            },
        )
        _record_completion(result)

        logger.info(
            f"Pi synthesis complete: {pending_count} events, "
            f"{tool_call_count} tool calls, "
            f"{total_duration}ms"
        )

        return result

    try:
        lock_handle, lock_path = _acquire_synthesis_lock(user_id)
    except SynthesisLockUnavailable:
        logger.info(
            "Skipping Pi synthesis because another cycle holds %s", _synthesis_lock_path(user_id)
        )
        result["status"] = "skipped"
        result["reason"] = "locked"
        result["events_processed"] = 0
        result["memex_updated"] = False
        result["duration_ms"] = int((time.time() - start_time) * 1000)
        ended_at = datetime.now(UTC)
        observer.record(
            observer_api.SYNTHESIS_SKIPPED,
            {
                "start_time": started_at.isoformat(),
                "end_time": ended_at.isoformat(),
                "duration_ms": int((ended_at - started_at).total_seconds() * 1000),
                "events_processed": 0,
                "cost_usd": 0.0,
                "reason": "locked",
                "lock_path": str(_synthesis_lock_path(user_id)),
            },
            run_id=run_id,
        )
        return result

    try:
        return _run_cycle_locked()
    finally:
        _release_synthesis_lock(lock_handle)
