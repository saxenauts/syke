"""
Pi-based agentic synthesis.

Uses the persistent Pi runtime to run synthesis cycles.
The agent operates in the workspace with full tool access:
- reads harness data via adapter markdowns in adapters/
- writes syke.db (canonical mutable database)
- updates MEMEX.md (routed workspace artifact)

Persistent runtime managed by the Syke daemon.
"""

from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TextIO

from uuid_extensions import uuid7

from syke.config import (
    CFG,
    FIRST_RUN_SYNC_TIMEOUT,
    user_data_dir,
)
from syke.db import SykeDB
from syke.llm.pi_client import resolve_pi_model
from syke.runtime.workspace import (
    MEMEX_PATH,
    SESSIONS_DIR,
    SYKE_DB,
    WORKSPACE_ROOT,
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
BOOTSTRAP_SKILL_PATH = Path(__file__).parent / "skills" / "pi_synthesis_bootstrap.md"

# MEMEX token budget — agent sees fill % in the header and self-regulates.
MEMEX_TOKEN_LIMIT = 4000
CHARS_PER_TOKEN = 4


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


def _load_bootstrap_skill_prompt() -> str:
    """Load the first-run bootstrap prompt fragment as static text."""
    if not BOOTSTRAP_SKILL_PATH.exists():
        raise FileNotFoundError(f"Bootstrap skill prompt not found: {BOOTSTRAP_SKILL_PATH}")
    return BOOTSTRAP_SKILL_PATH.read_text()


def _build_first_run_prompt(
    base_prompt: str,
    db: SykeDB,
    user_id: str,
    *,
    pending_count: int,
) -> str:
    """Augment the base synthesis prompt for bootstrap setup runs.

    First-run synthesis should form an initial memex from a broad view of newly
    ingested evidence, not behave like an ordinary incremental refresh with only
    a longer timeout.
    """
    sources = db.get_sources(user_id)
    source_lines: list[str] = []
    for source in sources:
        try:
            count = db.count_events(user_id, source)
        except Exception:
            count = 0
        source_lines.append(f"- {source}: {count} event{'s' if count != 1 else ''}")
    source_block = "\n".join(source_lines) if source_lines else "- no sources recorded yet"
    bootstrap_brief = (
        _load_bootstrap_skill_prompt()
        .replace("__PENDING_COUNT__", str(pending_count))
        .replace("__SOURCE_BLOCK__", source_block)
    )
    return bootstrap_brief + base_prompt


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
        body = _strip_memex_header(content)
        stats["memex_artifact_exists"] = True
        stats["memex_artifact_size"] = len(content)
        stats["memex_artifact_empty"] = not bool(content)
        token_estimate = len(body) // CHARS_PER_TOKEN
        stats["memex_tokens"] = token_estimate
        if token_estimate > MEMEX_TOKEN_LIMIT:
            issues.append(f"MEMEX over budget: {token_estimate}/{MEMEX_TOKEN_LIMIT} tokens")
            stats["memex_over_budget"] = True
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


def _strip_memex_header(content: str) -> str:
    """Remove the fill-indicator header line if present."""
    lines = content.split("\n")
    if lines and lines[0].startswith("# MEMEX ["):
        return "\n".join(lines[1:]).lstrip("\n")
    return content


def _inject_memex_header(content: str) -> str:
    """Prepend the token budget fill indicator."""
    body = _strip_memex_header(content)
    char_count = len(body)
    token_estimate = char_count // CHARS_PER_TOKEN
    fill_pct = min(100, round(token_estimate / MEMEX_TOKEN_LIMIT * 100))
    header = f"# MEMEX [{token_estimate:,} / {MEMEX_TOKEN_LIMIT:,} tokens · {fill_pct}%]"
    return header + "\n\n" + body


def _write_memex_artifact(content: str) -> bool:
    content_with_header = _inject_memex_header(content)
    existing = _read_memex_artifact()
    if existing == content_with_header:
        return False
    MEMEX_PATH.write_text(content_with_header + "\n", encoding="utf-8")
    return True


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
            observer.emit(
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
    success: bool = True,
    error: str | None = None,
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
                success=success,
                error=error,
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
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """
    Run one Pi synthesis cycle.

    The agent always runs. It receives temporal context (current time,
    last cycle time) and decides whether anything warrants updating.

    Flow:
    1. Setup/validate workspace
    2. Build skill prompt with temporal context
    3. Send to persistent Pi runtime
    5. Validate output
    6. Sync memex to Syke DB
    7. Record cycle

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
    observer.emit(
        observer_api.SYNTHESIS_START,
        {"start_time": started_at.isoformat()},
        run_id=run_id,
    )
    previous_memex_content = _current_memex_content(db, user_id)
    is_first_run = first_run if first_run is not None else previous_memex_content is None
    previous_memex_artifact_content = _read_memex_artifact()

    def _progress(message: str) -> None:
        if progress is not None:
            progress(message)

    def _record_completion(final_result: dict[str, object]) -> None:
        ended_at = datetime.now(UTC)
        observer.emit(
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
            },
            run_id=run_id,
        )

    def _run_cycle_locked() -> dict[str, object]:
        # ── 1. Setup workspace ──
        _progress("preparing workspace")
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        from syke.observe.bootstrap import ensure_adapters
        from syke.runtime.psyche_md import write_psyche_md

        ensure_adapters(WORKSPACE_ROOT)
        write_psyche_md(WORKSPACE_ROOT)

        if not WORKSPACE_ROOT.is_dir():
            logger.error("Workspace directory missing after mkdir")
            result["status"] = "failed"
            result["error"] = "Workspace directory missing"
            result["duration_ms"] = int((time.time() - start_time) * 1000)
            _record_completion(result)
            return result

        _progress("workspace ready")

        # ── 2. Build skill prompt ──
        if skill_override is not None:
            prompt = skill_override
        else:
            prompt = _load_skill_prompt()
        if is_first_run:
            prompt = _build_first_run_prompt(
                prompt,
                db,
                user_id,
                pending_count=0,
            )

        # Inject temporal context so the agent knows its time boundary
        import time as _time

        last_cycle_row = db.conn.execute(
            "SELECT completed_at FROM cycle_records"
            " WHERE user_id = ? AND status = 'completed'"
            " ORDER BY completed_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        now_local = datetime.now()
        tz_name = _time.tzname[_time.daylight] if _time.daylight else _time.tzname[0]

        if last_cycle_row and last_cycle_row[0]:
            last_dt = datetime.fromisoformat(last_cycle_row[0])
            last_local = last_dt.astimezone()
            gap_min = int((now_local - last_local.replace(tzinfo=None)).total_seconds() / 60)
            last_line = f"Last synthesis: {last_local.strftime('%Y-%m-%d %H:%M')} {tz_name} ({gap_min} min ago)"
        else:
            last_line = "Last synthesis: none (first run)"

        cycle_count = db.conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        prompt += (
            f"\n\n---\n"
            f"Now: {now_local.strftime('%Y-%m-%d %H:%M')} {tz_name}\n"
            f"{last_line}\n"
            f"Cycle: #{cycle_count + 1}\n"
        )

        pending_count = 0  # Agent detects changes itself via temporal context + adapters

        logger.info("Starting Pi synthesis cycle #%d", cycle_count + 1)
        _progress("starting synthesis")

        # ── 3. Record cycle start ──
        cycle_id = None
        try:
            cycle_id = db.insert_cycle_record(
                user_id=user_id,
                cursor_start=None,
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
        if is_first_run:
            timeout = max(timeout, FIRST_RUN_SYNC_TIMEOUT)

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

            if runtime_reused:
                _progress(f"reusing Pi runtime · {requested_model}")
            else:
                _progress(f"starting Pi runtime · {requested_model}")

            runtime = start_pi_runtime(
                workspace_dir=WORKSPACE_ROOT,
                session_dir=SESSIONS_DIR,
                model=model_override,
            )
            _progress(f"runtime ready · {requested_model}")

            def _on_runtime_event(event: dict[str, object]) -> None:
                event_type = event.get("type")
                if event_type in {"tool_execution_start", "tool_call"}:
                    tool = event.get("toolExecution")
                    if not isinstance(tool, dict):
                        tool = event.get("toolCall")
                    name = tool.get("name") if isinstance(tool, dict) else None
                    if isinstance(name, str) and name:
                        _progress(f"tool · {name}")
                    return
                if event_type == "response":
                    _progress("finalizing response")

            pi_result = runtime.prompt(
                prompt,
                timeout=timeout,
                new_session=True,
                on_event=_on_runtime_event,
            )
        except Exception as e:
            logger.exception("Pi runtime failed during synthesis cycle")
            failure_duration = int((time.time() - start_time) * 1000)
            result["status"] = "failed"
            result["error"] = f"Pi runtime failed: {e}"
            result["duration_ms"] = failure_duration
            result["events_processed"] = pending_count
            result["memex_updated"] = False
            result["runtime_reused"] = runtime_reused
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
                success=False,
                error=pi_result.error,
                details={
                    "status": "failed",
                    "success": False,
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
        _progress("syncing memex")
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
                success=False,
                error=str(result["error"]),
                details={
                    "status": "failed",
                    "success": False,
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
                },
            )
            _record_completion(result)
            return result

        # ── 9. Advance cursor ──
        # In v2, the agent manages its own cursor via markdown.
        # For compatibility, still advance the DB cursor using the cycle record.
        try:
            db.set_synthesis_cursor(user_id, cycle_id)
            logger.info(f"Cursor advanced to cycle {cycle_id}")
            _progress("cursor advanced")
        except Exception as e:
            logger.warning(f"Failed to advance cursor: {e}")

        # ── 10. Complete cycle record ──
        if cycle_id:
            try:
                db.complete_cycle_record(
                    cycle_id=cycle_id,
                    status="completed",
                    cursor_end=cycle_id,
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
            success=True,
            details={
                "status": "completed",
                "success": True,
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
        observer.emit(
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
