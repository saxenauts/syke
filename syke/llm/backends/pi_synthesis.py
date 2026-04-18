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

# MEMEX token budget — agent sees fill % in the header and self-regulates.
MEMEX_TOKEN_LIMIT = 4000
CHARS_PER_TOKEN = 4


class SynthesisLockUnavailable(RuntimeError):
    """Raised when another synthesis cycle already holds the user lock."""


class _SynthesisCommitFailed(RuntimeError):
    """Raised inside the post-synthesis transaction to trigger rollback."""


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
    # Atomic write: temp file then rename (POSIX rename is atomic).
    tmp = MEMEX_PATH.with_suffix(".tmp")
    tmp.write_text(content_with_header + "\n", encoding="utf-8")
    tmp.rename(MEMEX_PATH)
    return True


def _sync_memex_to_db(
    db: SykeDB,
    user_id: str,
    *,
    previous_content: str | None = None,
    previous_artifact_content: str | None = None,
) -> dict[str, object]:
    """Resolve canonical memex and project to MEMEX.md.

    The agent can update memex two ways: SQL into syke.db, or editing
    MEMEX.md directly.  Either path converges here — DB wins if both
    changed, otherwise a changed artifact is imported into the DB.
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
        canonical_content = _strip_memex_header(artifact_content)
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


# ── Main entry point ──────────────────────────────────────────────────


def pi_synthesize(
    db: SykeDB,
    user_id: str,
    *,
    skill_override: str | None = None,
    model_override: str | None = None,
    first_run: bool | None = None,
    progress: Callable[[str], None] | None = None,
    now_override: datetime | None = None,
    workspace_root: Path | None = None,
    home: Path | None = None,
    skill_path: Path | None = None,
) -> dict[str, object]:
    """
    Run one Pi synthesis cycle.

    The agent always runs. It receives temporal context (current time,
    last cycle time) and decides whether anything warrants updating.

    now_override: If set, use this as "now" instead of wall clock.
    Used by replay to simulate the correct time period for a dataset window.

    workspace_root: If set, use this workspace instead of the module-level
    WORKSPACE_ROOT. Eliminates the need for callers to monkey-patch globals.

    home: Passed to build_prompt → _build_psyche_md so adapter path
    discovery resolves relative to this directory instead of the real
    user home. Used by replay to scope PSYCHE to the workspace.

    skill_path: If set, build_prompt reads the skill from this file
    instead of the default SKILL_PATH. Used for ablation conditions
    (different synthesis prompts) without bypassing PSYCHE+MEMEX
    injection.

    Flow:
    1. Setup/validate workspace
    2. Build skill prompt with temporal context
    3. Send to persistent Pi runtime
    5. Validate output
    6. Sync memex to Syke DB
    7. Record cycle

    Returns dict with cycle results and metrics.
    """
    _ws_root = workspace_root or WORKSPACE_ROOT
    start_time = time.monotonic()
    result: dict[str, object] = {
        "backend": "pi",
        "status": "pending",
        "cost_usd": None,
        "input_tokens": None,
        "output_tokens": None,
        "duration_ms": None,
        "memex_updated": None,
        "num_turns": 0,
        "error": None,
        "reason": None,
    }
    run_id = str(uuid7())
    started_at = now_override.astimezone() if now_override else datetime.now(UTC)
    trace_id: str | None = None
    previous_memex_content = _current_memex_content(db, user_id)
    is_first_run = first_run if first_run is not None else previous_memex_content is None
    previous_memex_artifact_content = _read_memex_artifact()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start_time) * 1000)

    def _progress(message: str) -> None:
        if progress is not None:
            progress(message)

    def _persist_trace(
        *,
        status: str,
        error: str | None,
        output_text: str,
        thinking: list[str] | None,
        transcript: list[dict[str, object]] | None,
        tool_calls: list[dict[str, object]] | None,
        duration_ms: int,
        cost_usd: float | None,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        num_turns: int = 0,
        provider: str | None,
        model: str | None,
        response_id: str | None,
        stop_reason: str | None,
        runtime_reused: bool | None,
        runtime_status: dict[str, object] | None,
        extras: dict[str, object] | None = None,
    ) -> str | None:
        try:
            from syke.trace_store import persist_rollout_trace

            trace_id = persist_rollout_trace(
                db=db,
                user_id=user_id,
                run_id=run_id,
                kind="synthesis",
                started_at=started_at,
                completed_at=now_override.astimezone() if now_override else datetime.now(UTC),
                status=status,
                error=error,
                input_text=None,
                output_text=output_text,
                thinking=thinking,
                transcript=transcript,
                tool_calls=tool_calls,
                metrics={
                    "duration_ms": duration_ms,
                    "cost_usd": cost_usd,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read_tokens,
                    "cache_write_tokens": cache_write_tokens,
                },
                runtime={
                    "provider": provider,
                    "model": model,
                    "response_id": response_id,
                    "stop_reason": stop_reason,
                    "num_turns": num_turns,
                    "runtime_reused": runtime_reused,
                    "runtime_pid": runtime_status.get("pid")
                    if isinstance(runtime_status, dict)
                    else None,
                    "runtime_uptime_s": runtime_status.get("uptime_s")
                    if isinstance(runtime_status, dict)
                    else None,
                    "runtime_session_count": runtime_status.get("session_count")
                    if isinstance(runtime_status, dict)
                    else None,
                },
                extras=extras,
            )
            return trace_id
        except Exception:
            logger.debug("Failed to persist synthesis trace", exc_info=True)
            return None

    def _run_cycle_locked() -> dict[str, object]:
        # ── 1. Verify workspace ──
        if not _ws_root.is_dir():
            result["status"] = "failed"
            result["error"] = "Workspace not initialized. Run `syke setup`."
            result["duration_ms"] = _elapsed_ms()

            return result

        _progress("workspace ready")

        # ── 2. Build temporal context ──
        import time as _time

        last_cycle_row = db.conn.execute(
            "SELECT completed_at FROM cycle_records"
            " WHERE user_id = ? AND status = 'completed'"
            " ORDER BY completed_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        now_local = now_override or datetime.now()
        # When now_override is set, store simulated time as completed_at
        # so subsequent cycles see the right "Last cycle" timestamp
        # instead of wall-clock (which would leak real time).
        _completed_at_override = (
            now_local.astimezone().isoformat() if now_override else None
        )
        _started_at_override = (
            now_local.astimezone().isoformat() if now_override else None
        )
        tz_name = _time.tzname[_time.daylight] if _time.daylight else _time.tzname[0]

        offset = -_time.timezone if not _time.daylight else -_time.altzone
        utc_sign = "+" if offset >= 0 else "-"
        utc_hours = abs(offset) // 3600

        now_str = f"{now_local.strftime('%Y-%m-%d %H:%M')} {tz_name} (UTC{utc_sign}{utc_hours})"

        if last_cycle_row and last_cycle_row[0]:
            last_dt = datetime.fromisoformat(last_cycle_row[0])
            last_local = last_dt.astimezone()
            last_synthesis_str = f"{last_local.strftime('%Y-%m-%d %H:%M')} {tz_name}"
        else:
            last_synthesis_str = "none (first run)"

        cycle_count = db.conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        # ── 3. Build prompt: <psyche> + <memex> + <synthesis> ──
        if skill_override is not None:
            prompt = skill_override
        else:
            from syke.runtime.psyche_md import build_prompt

            prompt = build_prompt(
                _ws_root, db=db, user_id=user_id,
                context="synthesis", home=home,
                synthesis_path=skill_path,
                now=now_str,
                last_synthesis=last_synthesis_str,
                cycle=cycle_count + 1,
            )

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
                started_at_override=_started_at_override,
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
            failure_duration = _elapsed_ms()
            result["status"] = "failed"
            result["error"] = f"Pi runtime failed: {e}"
            result["duration_ms"] = failure_duration
            result["memex_updated"] = False
            result["runtime_reused"] = runtime_reused
            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id, status="failed", duration_ms=failure_duration,
                        completed_at_override=_completed_at_override,
                    )
                except Exception:
                    pass

            return result
        runtime_status = _safe_runtime_status(runtime)
        tool_names, tool_name_counts = _summarize_tools(pi_result.tool_calls)
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
            result["memex_updated"] = False
            trace_id = _persist_trace(
                status="failed",
                error=pi_result.error,
                output_text=pi_result.output,
                thinking=getattr(pi_result, "thinking", []) or [],
                transcript=transcript,
                tool_calls=pi_result.tool_calls,
                duration_ms=int(pi_result.duration_ms or 0),
                cost_usd=pi_result.cost_usd,
                input_tokens=pi_result.input_tokens,
                output_tokens=pi_result.output_tokens,
                cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                cache_write_tokens=int(pi_result.cache_write_tokens or 0),
                provider=pi_result.provider,
                model=pi_result.response_model,
                response_id=pi_result.response_id,
                stop_reason=pi_result.stop_reason,
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                extras={"memex_updated": False},
            )
            result["trace_id"] = trace_id

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
                        completed_at_override=_completed_at_override,
                    )
                except Exception:
                    pass

            return result

        # ── 7. Validate output ──
        validation = _validate_cycle_output()

        if not validation["valid"]:
            logger.warning(f"Cycle output validation issues: {validation['issues']}")

        # ── 7b. MEMEX budget enforcement ──
        # If over budget, give the agent up to 3 retries in the same session.
        # The agent has full context from the synthesis it just did.
        memex_retries = 0
        while validation.get("stats", {}).get("memex_over_budget") and memex_retries < 3:
            memex_retries += 1
            token_count = validation["stats"].get("memex_tokens", 0)
            logger.info(
                "MEMEX over budget (%d/%d tokens) — retry %d/3",
                token_count,
                MEMEX_TOKEN_LIMIT,
                memex_retries,
            )
            _progress(f"MEMEX over budget — compaction retry {memex_retries}/3")
            try:
                runtime.prompt(
                    f"MEMEX.md is {token_count}/{MEMEX_TOKEN_LIMIT} tokens — over budget. "
                    f"Compact it under {MEMEX_TOKEN_LIMIT} tokens. Move detail into memories, "
                    f"keep only pointers and one-line hooks in routes. "
                    f"Rewrite MEMEX.md now. Retry {memex_retries}/3.",
                    timeout=120,
                )
            except Exception as e:
                logger.warning("MEMEX compaction retry %d failed: %s", memex_retries, e)
                break
            validation = _validate_cycle_output()

        if validation.get("stats", {}).get("memex_over_budget"):
            token_count = validation["stats"].get("memex_tokens", 0)
            logger.error(
                "MEMEX still over budget after %d retries (%d/%d tokens) — cycle failed",
                memex_retries,
                token_count,
                MEMEX_TOKEN_LIMIT,
            )
            # Revert MEMEX to previous canonical content
            if previous_memex_artifact_content is not None:
                _write_memex_artifact(previous_memex_artifact_content)
            elif previous_memex_content is not None:
                _write_memex_artifact(previous_memex_content)

            result["status"] = "failed"
            result["error"] = (
                f"MEMEX over budget after {memex_retries} retries ({token_count}/{MEMEX_TOKEN_LIMIT} tokens)"
            )
            result["memex_updated"] = False
            result["duration_ms"] = _elapsed_ms()
            trace_id = _persist_trace(
                status="failed",
                error=str(result["error"]),
                output_text=pi_result.output,
                thinking=getattr(pi_result, "thinking", []) or [],
                transcript=transcript,
                tool_calls=pi_result.tool_calls,
                duration_ms=_elapsed_ms(),
                cost_usd=pi_result.cost_usd,
                input_tokens=pi_result.input_tokens,
                output_tokens=pi_result.output_tokens,
                cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                cache_write_tokens=int(pi_result.cache_write_tokens or 0),
                provider=pi_result.provider,
                model=pi_result.response_model,
                response_id=pi_result.response_id,
                stop_reason=pi_result.stop_reason,
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                extras={"memex_updated": False},
            )
            result["trace_id"] = trace_id

            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id,
                        status="failed",
                        cost_usd=float(pi_result.cost_usd or 0.0),
                        input_tokens=int(pi_result.input_tokens or 0),
                        output_tokens=int(pi_result.output_tokens or 0),
                        cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                        duration_ms=_elapsed_ms(),
                        completed_at_override=_completed_at_override,
                    )
                except Exception:
                    pass

            return result

        # ── 8–10. Atomic post-synthesis commit ──
        # Memex sync and cycle completion in one transaction.
        # Either all DB writes succeed or all roll back.
        # Note: MEMEX.md file write is a side effect inside the transaction
        # (atomic via temp+rename). If the transaction rolls back, the file
        # may be ahead of the DB — acceptable since it's a projection, not
        # source of truth, and next cycle re-projects.
        _progress("syncing memex")
        memex_synced = False
        memex_updated = False
        total_duration = _elapsed_ms()
        try:
            with db.transaction():
                memex_sync = _sync_memex_to_db(
                    db,
                    user_id,
                    previous_content=previous_memex_content,
                    previous_artifact_content=previous_memex_artifact_content,
                )
                memex_synced = bool(memex_sync.get("ok", False))
                memex_updated = bool(memex_sync.get("updated", False))

                if not memex_synced:
                    # Empty-memex tolerance: if SYKE_ALLOW_EMPTY_MEMEX is
                    # set (replay ablations like the Hyperagent-style zero
                    # prompt), record the cycle as completed-but-no-update
                    # instead of rolling back the transaction. The cycle
                    # still ran and consumed budget — we measure it.
                    if os.environ.get("SYKE_ALLOW_EMPTY_MEMEX"):
                        logger.warning(
                            "Memex sync produced no content; continuing "
                            "(SYKE_ALLOW_EMPTY_MEMEX)"
                        )
                    else:
                        raise _SynthesisCommitFailed(
                            "Pi synthesis completed but canonical memex is unavailable"
                        )

                if cycle_id:
                    db.complete_cycle_record(
                        cycle_id=cycle_id,
                        status="completed",
                        cursor_end=cycle_id,
                        memex_updated=memex_updated,
                        cost_usd=float(pi_result.cost_usd or 0.0),
                        input_tokens=int(pi_result.input_tokens or 0),
                        output_tokens=int(pi_result.output_tokens or 0),
                        cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                        duration_ms=total_duration,
                        completed_at_override=_completed_at_override,
                    )
            logger.info(f"Post-synthesis commit for cycle {cycle_id}")
        except _SynthesisCommitFailed as e:
            # Memex sync failed — transaction rolled back.
            logger.error("%s; transaction rolled back", e)
            result["status"] = "failed"
            result["error"] = str(e)
            result["memex_updated"] = False
            result["duration_ms"] = total_duration
            result["cost_usd"] = pi_result.cost_usd
            result["input_tokens"] = pi_result.input_tokens
            result["output_tokens"] = pi_result.output_tokens
            trace_id = _persist_trace(
                status="failed",
                error=str(e),
                output_text=pi_result.output,
                thinking=getattr(pi_result, "thinking", []) or [],
                transcript=transcript,
                tool_calls=pi_result.tool_calls,
                duration_ms=total_duration,
                cost_usd=pi_result.cost_usd,
                input_tokens=pi_result.input_tokens,
                output_tokens=pi_result.output_tokens,
                cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                cache_write_tokens=int(pi_result.cache_write_tokens or 0),
                provider=pi_result.provider,
                model=pi_result.response_model,
                response_id=pi_result.response_id,
                stop_reason=pi_result.stop_reason,
                runtime_reused=runtime_reused,
                runtime_status=runtime_status,
                extras={"memex_updated": False},
            )
            result["trace_id"] = trace_id

            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id,
                        status="failed",
                        memex_updated=False,
                        cost_usd=float(pi_result.cost_usd or 0.0),
                        input_tokens=int(pi_result.input_tokens or 0),
                        output_tokens=int(pi_result.output_tokens or 0),
                        cache_read_tokens=int(pi_result.cache_read_tokens or 0),
                        completed_at_override=_completed_at_override,
                        duration_ms=total_duration,
                    )
                except Exception:
                    pass

            return result
        except Exception as e:
            logger.warning(f"Failed to commit post-synthesis state: {e}")

        result["status"] = "completed"
        result["memex_updated"] = memex_updated
        result["duration_ms"] = total_duration
        result["cost_usd"] = pi_result.cost_usd
        result["input_tokens"] = pi_result.input_tokens
        result["output_tokens"] = pi_result.output_tokens
        trace_id = _persist_trace(
            status="completed",
            error=None,
            output_text=pi_result.output,
            thinking=getattr(pi_result, "thinking", []) or [],
            transcript=transcript,
            tool_calls=pi_result.tool_calls,
            duration_ms=total_duration,
            cost_usd=pi_result.cost_usd,
            input_tokens=pi_result.input_tokens,
            output_tokens=pi_result.output_tokens,
            cache_read_tokens=int(pi_result.cache_read_tokens or 0),
            cache_write_tokens=int(pi_result.cache_write_tokens or 0),
            num_turns=num_turns,
            provider=pi_result.provider,
            model=pi_result.response_model,
            response_id=pi_result.response_id,
            stop_reason=pi_result.stop_reason,
            runtime_reused=runtime_reused,
            runtime_status=runtime_status,
            extras={"memex_updated": memex_updated},
        )
        result["trace_id"] = trace_id

        logger.info(f"Pi synthesis complete: {tool_call_count} tool calls, {total_duration}ms")

        return result

    try:
        lock_handle, lock_path = _acquire_synthesis_lock(user_id)
    except SynthesisLockUnavailable:
        logger.info(
            "Skipping Pi synthesis because another cycle holds %s", _synthesis_lock_path(user_id)
        )
        result["status"] = "skipped"
        result["reason"] = "locked"
        result["memex_updated"] = False
        result["duration_ms"] = _elapsed_ms()
        return result

    try:
        return _run_cycle_locked()
    finally:
        _release_synthesis_lock(lock_handle)
