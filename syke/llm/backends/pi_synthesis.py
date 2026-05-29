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

import hashlib
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
from syke.db_safety import (
    RecoveryPoint,
    StateBaseline,
    capture_baseline,
    create_recovery_point,
    restore_recovery_point,
    rotate_recovery_points,
    validate_state_after_cycle,
)
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
MEMEX_TOKEN_LIMIT = 2000
CHARS_PER_TOKEN = 4
STALE_RUNNING_CYCLE_SECONDS = 6 * 60 * 60


class SynthesisLockUnavailable(RuntimeError):
    """Raised when another synthesis cycle already holds the user lock."""


class _SynthesisCommitFailed(RuntimeError):
    """Raised inside the post-synthesis transaction to trigger rollback."""


_EMPTY_FIRST_MEMEX_MARKERS = (
    "no durable user/project memories",
    "no durable memories",
    "no memories have been recorded",
    "no harness adapters",
    "no adapters installed",
)


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
    stats["syke_db_path"] = str(SYKE_DB)
    stats["sqlite_module_version"] = sqlite3.sqlite_version
    stats["syke_db_sidecars"] = {
        candidate.name: candidate.stat().st_size
        for candidate in (SYKE_DB, Path(f"{SYKE_DB}-wal"), Path(f"{SYKE_DB}-shm"))
        if candidate.exists()
    }
    if SYKE_DB.exists() and SYKE_DB.stat().st_size > 0:
        try:
            conn = sqlite3.connect(f"file:{SYKE_DB}?mode=ro", uri=True, timeout=5)
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
                integrity = conn.execute("PRAGMA integrity_check").fetchone()
                quick = conn.execute("PRAGMA quick_check").fetchone()
                stats["integrity_check"] = integrity[0] if integrity else None
                stats["quick_check"] = quick[0] if quick else None
                if stats["integrity_check"] != "ok":
                    issues.append(f"syke.db integrity_check: {stats['integrity_check']}")
                if stats["quick_check"] != "ok":
                    issues.append(f"syke.db quick_check: {stats['quick_check']}")
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


def _db_validation_issues(validation: dict[str, object]) -> list[str]:
    issues = validation.get("issues")
    if not isinstance(issues, list):
        return []
    return [
        str(issue)
        for issue in issues
        if str(issue).startswith(
            (
                "syke.db read error",
                "syke.db integrity_check",
                "syke.db quick_check",
            )
        )
    ]


# ── Memex authority: canonical DB + routed workspace artifact ───────


def _current_memex_content(db: SykeDB, user_id: str) -> str | None:
    return _memex_content(_current_memex_row(db, user_id))


def _current_memex_row(db: SykeDB, user_id: str) -> dict[str, object] | None:
    return db.get_memex(user_id)


def _memex_content(memex: dict[str, object] | None) -> str | None:
    if not memex:
        return None
    content = memex.get("content")
    return content if isinstance(content, str) and content.strip() else None


def _read_memex_artifact() -> str | None:
    if not MEMEX_PATH.exists():
        return None
    content = MEMEX_PATH.read_text(encoding="utf-8").strip()
    return content or None


def _restore_memex_artifact(
    previous_artifact_content: str | None,
    previous_content: str | None,
) -> None:
    """Undo a rejected projection so the next cycle does not import it."""
    if previous_artifact_content is not None:
        _write_memex_artifact(previous_artifact_content)
        return
    if previous_content is not None:
        _write_memex_artifact(previous_content)
        return
    MEMEX_PATH.unlink(missing_ok=True)


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


def _memex_bodies_match(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    return _strip_memex_header(left).strip() == _strip_memex_header(right).strip()


def _normalize_current_memex_projection_header(
    db: SykeDB,
    user_id: str,
    memex: dict[str, object] | None,
) -> dict[str, object] | None:
    content = _memex_content(memex)
    if content is None or _strip_memex_header(content) == content:
        return memex
    from syke.memory.memex import update_memex

    update_memex(db, user_id, _strip_memex_header(content))
    return _current_memex_row(db, user_id)


def _write_memex_artifact(content: str) -> bool:
    content_with_header = _inject_memex_header(content)
    existing = _read_memex_artifact()
    if existing == content_with_header.strip():
        return False
    # Atomic write: temp file then rename (POSIX rename is atomic).
    tmp = MEMEX_PATH.with_suffix(".tmp")
    tmp.write_text(content_with_header + "\n", encoding="utf-8")
    tmp.rename(MEMEX_PATH)
    return True


def _empty_first_run_memex(started_at: datetime) -> str:
    local_time = started_at.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return (
        f"As of {local_time}:\n\n"
        "- No durable user/project memories have been captured yet.\n"
        "- No prior harness history was detected during first synthesis.\n"
        "- Syke is ready for future harness activity, `syke record`, and `syke ask`.\n"
        "- This MEMEX will grow after real events are observed."
    )


def _sync_memex_to_db(
    db: SykeDB,
    user_id: str,
    *,
    previous_content: str | None = None,
    previous_id: str | None = None,
    previous_updated_at: str | None = None,
    previous_artifact_content: str | None = None,
    empty_first_run_content: str | None = None,
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

    current_memex = _normalize_current_memex_projection_header(
        db,
        user_id,
        _current_memex_row(db, user_id),
    )
    current_content = _memex_content(current_memex)
    current_id = str(current_memex.get("id")) if current_memex and current_memex.get("id") else None
    artifact_content = _read_memex_artifact()
    db_changed_during_cycle = current_content != previous_content
    artifact_changed_during_cycle = artifact_content != previous_artifact_content

    if db_changed_during_cycle and current_content is not None:
        canonical_content = current_content
        result["source"] = "db"
        if previous_id and current_id == previous_id and previous_content is not None:
            # Agents can mutate the active MEMEX row directly. Convert that
            # in-place edit into a real supersession so history/projection
            # invariants do not depend on trusting the agent's claim.
            db.conn.execute(
                """UPDATE memories
                   SET content = ?, updated_at = ?
                   WHERE user_id = ? AND id = ?""",
                (previous_content, previous_updated_at, user_id, previous_id),
            )
            update_memex(db, user_id, canonical_content)
            result["normalized_in_place"] = True
            current_memex = _current_memex_row(db, user_id)
            current_content = _memex_content(current_memex)
            if current_content is None:
                logger.error("In-place memex normalization left canonical memex missing")
                return result
            canonical_content = current_content
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
    elif previous_content is not None:
        canonical_content = previous_content
        result["source"] = "previous"
        try:
            update_memex(db, user_id, canonical_content)
            logger.warning(
                "Canonical memex was missing after synthesis; restored previous memex (%d chars)",
                len(canonical_content),
            )
        except Exception as e:
            logger.error(f"Failed to restore previous canonical memex: {e}")
            return result
        current_content = _current_memex_content(db, user_id)
        if current_content is None:
            logger.error("Previous memex restore completed but canonical memex is still missing")
            return result
        canonical_content = current_content
    elif empty_first_run_content is not None:
        canonical_content = empty_first_run_content
        result["source"] = "empty_first_run"
        try:
            update_memex(db, user_id, canonical_content)
            logger.info("Recorded empty first-run MEMEX state (%d chars)", len(canonical_content))
        except Exception as e:
            logger.error(f"Failed to record empty first-run MEMEX state: {e}")
            return result
        current_content = _current_memex_content(db, user_id)
        if current_content is None:
            logger.error("Empty first-run MEMEX write completed but canonical memex is missing")
            return result
        canonical_content = current_content
    else:
        logger.error("No canonical memex available after synthesis")
        return result

    try:
        result["artifact_written"] = _write_memex_artifact(canonical_content)
        if not _memex_bodies_match(_read_memex_artifact(), canonical_content):
            logger.error("Projected MEMEX.md does not match canonical memex content")
            result["source"] = "artifact_mismatch"
            return result
        result["updated"] = canonical_content != previous_content
        if result["updated"] and previous_id:
            active_memex = _current_memex_row(db, user_id)
            active_id = (
                str(active_memex.get("id")) if active_memex and active_memex.get("id") else None
            )
            if active_id == previous_id:
                logger.error("MEMEX content changed without a new canonical row")
                result["source"] = "unversioned_update"
                result["updated"] = False
                return result
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


def _active_non_memex_memory_count(db: SykeDB, user_id: str) -> int:
    row = db.conn.execute(
        "SELECT COUNT(*) FROM memories "
        "WHERE user_id = ? AND active = 1 "
        "AND (source_event_ids IS NULL OR source_event_ids != ?)",
        (user_id, '["__memex__"]'),
    ).fetchone()
    return int(row[0] if row else 0)


def _discovered_source_file_counts(
    selected_sources: tuple[str, ...] | None,
    *,
    home: Path | None = None,
) -> dict[str, int]:
    from syke.observe.catalog import active_sources, iter_discovered_files

    selected_set = set(selected_sources) if selected_sources is not None else None
    counts: dict[str, int] = {}
    for spec in active_sources():
        if selected_set is not None and spec.source not in selected_set:
            continue
        try:
            count = len(iter_discovered_files(spec, home=home))
        except OSError:
            logger.debug("Source discovery failed for %s", spec.source, exc_info=True)
            continue
        if count:
            counts[spec.source] = count
    return counts


def _looks_like_empty_first_memex(content: str | None) -> bool:
    if not content:
        return True
    body = _strip_memex_header(content).lower()
    return any(marker in body for marker in _EMPTY_FIRST_MEMEX_MARKERS)


def _first_run_bootstrap_prompt(source_file_counts: dict[str, int]) -> str:
    source_lines = "\n".join(
        f"- {source}: {count} discovered files/rows"
        for source, count in sorted(source_file_counts.items())
    )
    return f"""

<first_run_bootstrap>
This is the first synthesis for this Syke workspace and local harness history exists.

Detected source inventory:
{source_lines}

Use the bootstrap path, not the steady-state shortcut:
- Read the selected adapter markdowns in `adapters/`.
- Follow the listed source roots directly.
- Count/list newest files or rows before sampling.
- Sample recent sessions from each selected source until you can identify stable
  threads, decisions, projects, or active questions.
- Create or update durable memory rows for strands that should survive future cycles.
- Write MEMEX as a navigable first map: sources, time windows, active routes,
  evidence roots, and what the user's agents can ask Syke for next.

Do not write an empty MEMEX merely because adapter markdown exists. Adapter
presence is not memory. Only write "no durable memories" after this bounded
survey finds no usable harness history, and then include which sources and paths
were checked.
</first_run_bootstrap>"""


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
    selected_sources: tuple[str, ...] | None = None,
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
    started_at = now_override if now_override else datetime.now(UTC)
    previous_memex = _current_memex_row(db, user_id)
    previous_memex_content = _memex_content(previous_memex)
    previous_memex_id = (
        str(previous_memex.get("id")) if previous_memex and previous_memex.get("id") else None
    )
    previous_memex_updated_at = (
        str(previous_memex.get("updated_at"))
        if previous_memex and previous_memex.get("updated_at")
        else None
    )
    is_first_run = first_run if first_run is not None else previous_memex_content is None
    previous_memex_artifact_content = _read_memex_artifact()
    pre_non_memex_memory_count = _active_non_memex_memory_count(db, user_id)
    first_run_source_file_counts = (
        _discovered_source_file_counts(selected_sources, home=home) if is_first_run else {}
    )

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start_time) * 1000)

    def _progress(message: str) -> None:
        if progress is not None:
            progress(message)

    def _pause_db_connection_for_agent() -> bool:
        if not os.environ.get("SYKE_REPLAY_PAUSE_DB_CONNECTION_DURING_PI"):
            return False
        if getattr(db, "db_path", ":memory:") == ":memory:":
            return False
        try:
            db.conn.commit()
            db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.close()
            logger.info("Replay DB connection paused while Pi agent runs")
            return True
        except Exception:
            logger.warning("Failed to pause replay DB connection before Pi", exc_info=True)
            return False

    def _resume_db_connection_after_agent(paused: bool) -> None:
        if not paused:
            return
        _reopen_db_connection()
        logger.info("Replay DB connection resumed after Pi agent run")

    def _reopen_db_connection() -> None:
        db._conn = db._connect_db(db.db_path)  # type: ignore[attr-defined]
        db._in_transaction = False  # type: ignore[attr-defined]
        db.initialize()

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
                completed_at=now_override if now_override else datetime.now(UTC),
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

    def _restore_recovery_point(point: RecoveryPoint) -> dict[str, object]:
        try:
            db.close()
        except Exception:
            logger.debug("Failed to close DB before recovery restore", exc_info=True)
        try:
            restore_info = restore_recovery_point(point)
        finally:
            _reopen_db_connection()
        _restore_memex_artifact(previous_memex_artifact_content, previous_memex_content)
        return restore_info

    def _fail_after_restore(
        *,
        error: str,
        recovery_point: RecoveryPoint | None,
        cycle_id: str | None,
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
        provider: str | None,
        model: str | None,
        response_id: str | None,
        stop_reason: str | None,
        runtime_reused: bool | None,
        runtime_status: dict[str, object] | None,
        extras: dict[str, object] | None = None,
        completed_at_override: str | None = None,
    ) -> dict[str, object]:
        restore_info: dict[str, object] | None = None
        if recovery_point is not None:
            try:
                restore_info = _restore_recovery_point(recovery_point)
                logger.error(
                    "Restored syke.db from recovery point %s after failure",
                    recovery_point.id,
                )
            except Exception as restore_error:
                logger.error(
                    "Failed to restore recovery point after synthesis failure",
                    exc_info=True,
                )
                result["recovery_error"] = str(restore_error)

        result["status"] = "failed"
        result["error"] = error
        result["memex_updated"] = False
        result["duration_ms"] = duration_ms
        result["cost_usd"] = cost_usd
        result["input_tokens"] = input_tokens
        result["output_tokens"] = output_tokens
        if restore_info is not None:
            result["recovery"] = restore_info

        if cycle_id:
            try:
                db.complete_cycle_record(
                    cycle_id=cycle_id,
                    status="failed",
                    memex_updated=False,
                    cost_usd=float(cost_usd or 0.0),
                    input_tokens=int(input_tokens or 0),
                    output_tokens=int(output_tokens or 0),
                    cache_read_tokens=int(cache_read_tokens or 0),
                    duration_ms=duration_ms,
                    completed_at_override=completed_at_override,
                )
            except Exception:
                logger.debug("Failed to mark restored cycle failed", exc_info=True)

        trace_extras = {"memex_updated": False, **(extras or {})}
        if recovery_point is not None:
            trace_extras["recovery_point"] = recovery_point.id
            trace_extras["recovery_backup_path"] = recovery_point.backup_path
            trace_extras["recovery_manifest_path"] = recovery_point.manifest_path
            trace_extras["recovery_method"] = recovery_point.method
            trace_extras["recovery_size_bytes"] = recovery_point.size_bytes
            trace_extras["recovery_restored"] = restore_info is not None
        if restore_info is not None:
            trace_extras["recovery"] = restore_info
        trace_id = _persist_trace(
            status="failed",
            error=error,
            output_text=output_text,
            thinking=thinking,
            transcript=transcript,
            tool_calls=tool_calls,
            duration_ms=duration_ms,
            cost_usd=cost_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            provider=provider,
            model=model,
            response_id=response_id,
            stop_reason=stop_reason,
            runtime_reused=runtime_reused,
            runtime_status=runtime_status,
            extras=trace_extras,
        )
        result["trace_id"] = trace_id
        return result

    def _run_cycle_locked() -> dict[str, object]:
        # ── 1. Verify workspace ──
        if not _ws_root.is_dir():
            result["status"] = "failed"
            result["error"] = "Workspace not initialized. Run `syke setup`."
            result["duration_ms"] = _elapsed_ms()

            return result

        _progress("workspace ready")

        try:
            requested_model = resolve_pi_model(model_override)
        except RuntimeError as exc:
            blocked_duration = _elapsed_ms()
            result["status"] = "blocked"
            result["reason"] = "setup_blocked"
            result["error"] = str(exc)
            result["duration_ms"] = blocked_duration
            result["memex_updated"] = False
            trace_id = _persist_trace(
                status="blocked",
                error=str(exc),
                output_text="",
                thinking=[],
                transcript=[],
                tool_calls=[],
                duration_ms=blocked_duration,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                provider=None,
                model=model_override,
                response_id=None,
                stop_reason=None,
                runtime_reused=None,
                runtime_status=None,
                extras={"memex_updated": False, "reason": "setup_blocked"},
            )
            result["trace_id"] = trace_id
            try:
                blocked_cycle_id = db.insert_cycle_record(
                    user_id=user_id,
                    cursor_start=None,
                    skill_hash="pi_synthesis",
                    prompt_hash="setup_blocked",
                    model=model_override or "pi",
                    started_at_override=started_at.isoformat(),
                )
                db.complete_cycle_record(
                    cycle_id=blocked_cycle_id,
                    status="blocked",
                    duration_ms=blocked_duration,
                    completed_at_override=(now_override.isoformat() if now_override else None),
                )
                result["cycle_id"] = blocked_cycle_id
            except Exception:
                logger.debug("Failed to persist blocked synthesis cycle", exc_info=True)
            logger.info("Pi synthesis blocked before cycle start: %s", exc)
            return result

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
        _completed_at_override = now_local.isoformat() if now_override else None
        _started_at_override = now_local.isoformat() if now_override else None
        from syke.runtime.psyche_md import format_now_for_prompt

        now_str = format_now_for_prompt(now_local)
        tz_name = _time.tzname[_time.daylight] if _time.daylight else _time.tzname[0]

        if last_cycle_row and last_cycle_row[0]:
            from syke.runtime.psyche_md import format_gap

            last_dt = datetime.fromisoformat(last_cycle_row[0])
            last_local = last_dt.astimezone() if last_dt.tzinfo else last_dt
            now_naive = now_local.replace(tzinfo=None) if now_local.tzinfo else now_local
            last_naive = last_local.replace(tzinfo=None)
            gap_str = format_gap(now_naive - last_naive)
            last_synthesis_str = f"{last_local.strftime('%Y-%m-%d %H:%M')} {tz_name} ({gap_str})"
        else:
            last_synthesis_str = "none (first run)"

        cycle_count = db.conn.execute(
            "SELECT COUNT(*) FROM cycle_records WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

        # ── 3. Build prompt: <psyche> + <now> + <memex> + <synthesis> ──
        if skill_override is not None:
            prompt = skill_override
        else:
            from syke.runtime.psyche_md import build_prompt

            prompt = build_prompt(
                _ws_root,
                db=db,
                user_id=user_id,
                context="synthesis",
                home=home,
                synthesis_path=skill_path,
                now=now_str,
                last_synthesis=last_synthesis_str,
                cycle=cycle_count + 1,
                selected_sources=selected_sources,
            )
            if is_first_run and first_run_source_file_counts:
                prompt += _first_run_bootstrap_prompt(first_run_source_file_counts)

        logger.info("Starting Pi synthesis cycle #%d", cycle_count + 1)
        _progress("starting synthesis")

        try:
            stale_cutoff = now_local - timedelta(seconds=STALE_RUNNING_CYCLE_SECONDS)
            stale_cycles = db.mark_stale_running_cycles(
                user_id,
                started_before=stale_cutoff.isoformat(),
                completed_at_override=_completed_at_override,
            )
            if stale_cycles:
                logger.warning("Marked %d stale running synthesis cycles incomplete", stale_cycles)
        except Exception:
            logger.debug("Failed to mark stale running synthesis cycles", exc_info=True)

        # ── 3. Record cycle start ──
        cycle_id = None
        recovery_point: RecoveryPoint | None = None
        safety_baseline: StateBaseline | None = None
        try:
            cycle_id = db.insert_cycle_record(
                user_id=user_id,
                cursor_start=None,
                skill_hash="pi_synthesis",
                prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
                model=model_override or "pi",
                started_at_override=_started_at_override,
            )
        except Exception as e:
            logger.warning(f"Failed to record cycle start: {e}")

        try:
            safety_baseline = capture_baseline(db, user_id)
            recovery_point = create_recovery_point(
                db,
                user_id,
                run_id=run_id,
                cycle_id=cycle_id,
                baseline=safety_baseline,
            )
            rotate_recovery_points(user_id)
            _progress("recovery point ready")
        except Exception as e:
            logger.exception("Failed to create recovery point before synthesis")
            duration_ms = _elapsed_ms()
            error = f"Failed to create recovery point before synthesis: {e}"
            result["status"] = "failed"
            result["error"] = error
            result["duration_ms"] = duration_ms
            result["memex_updated"] = False
            if cycle_id:
                try:
                    db.complete_cycle_record(
                        cycle_id=cycle_id,
                        status="failed",
                        memex_updated=False,
                        duration_ms=duration_ms,
                        completed_at_override=_completed_at_override,
                    )
                except Exception:
                    logger.debug(
                        "Failed to mark cycle failed after recovery setup error",
                        exc_info=True,
                    )
            trace_id = _persist_trace(
                status="failed",
                error=error,
                output_text="",
                thinking=[],
                transcript=[],
                tool_calls=[],
                duration_ms=duration_ms,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                provider=None,
                model=model_override,
                response_id=None,
                stop_reason=None,
                runtime_reused=None,
                runtime_status=None,
                extras={"memex_updated": False, "reason": "recovery_point_failed"},
            )
            result["trace_id"] = trace_id
            return result

        # ── 5. Send to Pi runtime ──
        timeout = 300  # 5 minutes default
        if CFG and hasattr(CFG, "synthesis") and CFG.synthesis:
            timeout = getattr(CFG.synthesis, "timeout", 300)
        if is_first_run:
            timeout = max(timeout, FIRST_RUN_SYNC_TIMEOUT)

        runtime_reused = False
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
                selected_sources=selected_sources,
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

            db_paused_for_agent = _pause_db_connection_for_agent()
            try:
                pi_result = runtime.prompt(
                    prompt,
                    timeout=timeout,
                    new_session=True,
                    on_event=_on_runtime_event,
                )
            finally:
                _resume_db_connection_after_agent(db_paused_for_agent)
        except Exception as e:
            logger.exception("Pi runtime failed during synthesis cycle")
            failure_duration = _elapsed_ms()
            result["runtime_reused"] = runtime_reused
            return _fail_after_restore(
                error=f"Pi runtime failed: {e}",
                recovery_point=recovery_point,
                cycle_id=cycle_id,
                output_text="",
                thinking=[],
                transcript=[],
                tool_calls=[],
                duration_ms=failure_duration,
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                provider=None,
                model=model_override,
                response_id=None,
                stop_reason=None,
                runtime_reused=runtime_reused,
                runtime_status=None,
                extras={"reason": "runtime_failed"},
                completed_at_override=_completed_at_override,
            )
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

        def _fail_cycle_for_db_validation(
            validation: dict[str, object],
            issues: list[str],
        ) -> dict[str, object] | None:
            if not issues:
                return None
            error = "Cycle DB validation failed: " + "; ".join(issues)
            logger.error(error)
            result["validation"] = validation
            return _fail_after_restore(
                error=error,
                recovery_point=recovery_point,
                cycle_id=cycle_id,
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
                extras={"validation": validation, "reason": "db_validation_failed"},
                completed_at_override=_completed_at_override,
            )

        if not pi_result.ok:
            logger.error(f"Pi synthesis failed: {pi_result.error}")
            return _fail_after_restore(
                error=str(pi_result.error or "Pi synthesis failed"),
                recovery_point=recovery_point,
                cycle_id=cycle_id,
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
                extras={"reason": "pi_result_failed"},
                completed_at_override=_completed_at_override,
            )

        # ── 7. Validate output ──
        validation = _validate_cycle_output()
        result["validation"] = validation

        if not validation["valid"]:
            logger.warning(f"Cycle output validation issues: {validation['issues']}")
            failed_validation = _fail_cycle_for_db_validation(
                validation,
                _db_validation_issues(validation),
            )
            if failed_validation is not None:
                return failed_validation

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
            result["validation"] = validation
            if not validation["valid"]:
                logger.warning(f"Cycle output validation issues: {validation['issues']}")
                failed_validation = _fail_cycle_for_db_validation(
                    validation,
                    _db_validation_issues(validation),
                )
                if failed_validation is not None:
                    return failed_validation

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

            error = (
                f"MEMEX over budget after {memex_retries} retries "
                f"({token_count}/{MEMEX_TOKEN_LIMIT} tokens)"
            )
            return _fail_after_restore(
                error=error,
                recovery_point=recovery_point,
                cycle_id=cycle_id,
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
                extras={
                    "reason": "memex_over_budget",
                    "validation": validation,
                    "memex_retries": memex_retries,
                },
                completed_at_override=_completed_at_override,
            )

        # ── 8–10. Post-synthesis commit + semantic gate ──
        # MEMEX sync happens before the final gate because it mutates the same
        # canonical state the gate protects. The cycle is marked completed only
        # after the gate passes.
        # Note: MEMEX.md file write is a side effect inside the transaction
        # (atomic via temp+rename). If the transaction rolls back, the file
        # may be ahead of the DB — acceptable since it's a projection, not
        # source of truth, and next cycle re-projects.
        _progress("syncing memex")
        empty_first_run_content = None
        if is_first_run and not first_run_source_file_counts and pre_non_memex_memory_count == 0:
            empty_first_run_content = _empty_first_run_memex(started_at)
        memex_synced = False
        memex_updated = False
        total_duration = _elapsed_ms()
        cycle_completed_at = _completed_at_override or datetime.now(UTC).isoformat()
        try:
            with db.transaction():
                memex_sync = _sync_memex_to_db(
                    db,
                    user_id,
                    previous_content=previous_memex_content,
                    previous_id=previous_memex_id,
                    previous_updated_at=previous_memex_updated_at,
                    previous_artifact_content=previous_memex_artifact_content,
                    empty_first_run_content=empty_first_run_content,
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
                            "Memex sync produced no content; continuing (SYKE_ALLOW_EMPTY_MEMEX)"
                        )
                    else:
                        raise _SynthesisCommitFailed(
                            "Pi synthesis completed but canonical memex is unavailable"
                        )

                if (
                    memex_synced
                    and is_first_run
                    and first_run_source_file_counts
                    and not os.environ.get("SYKE_ALLOW_EMPTY_MEMEX")
                ):
                    active_non_memex_after = _active_non_memex_memory_count(db, user_id)
                    current_memex = _current_memex_content(db, user_id)
                    if (
                        active_non_memex_after <= pre_non_memex_memory_count
                        and _looks_like_empty_first_memex(current_memex)
                    ):
                        _restore_memex_artifact(
                            previous_memex_artifact_content,
                            previous_memex_content,
                        )
                        sources = ", ".join(
                            f"{source}={count}"
                            for source, count in sorted(first_run_source_file_counts.items())
                        )
                        raise _SynthesisCommitFailed(
                            "First synthesis produced an empty MEMEX despite detected "
                            f"harness history ({sources}). Bootstrap is incomplete; "
                            "run `syke sync` again after checking adapter roots."
                        )

            if safety_baseline is not None:
                semantic_gate = validate_state_after_cycle(
                    db,
                    user_id,
                    safety_baseline,
                    allow_empty_memex=bool(os.environ.get("SYKE_ALLOW_EMPTY_MEMEX")),
                    memex_token_limit=MEMEX_TOKEN_LIMIT,
                    chars_per_token=CHARS_PER_TOKEN,
                )
                result["semantic_gate"] = semantic_gate
                if not semantic_gate.get("valid", False):
                    issues = semantic_gate.get("issues")
                    issue_text = (
                        "; ".join(str(issue) for issue in issues)
                        if isinstance(issues, list)
                        else "unknown"
                    )
                    error = f"Cycle semantic gate failed: {issue_text}"
                    logger.error(error)
                    return _fail_after_restore(
                        error=error,
                        recovery_point=recovery_point,
                        cycle_id=cycle_id,
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
                        extras={"semantic_gate": semantic_gate, "reason": "semantic_gate_failed"},
                        completed_at_override=_completed_at_override,
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
                    completed_at_override=cycle_completed_at,
                )
            logger.info(f"Post-synthesis commit for cycle {cycle_id}")
        except _SynthesisCommitFailed as e:
            # Memex sync failed — transaction rolled back.
            logger.error("%s; transaction rolled back", e)
            return _fail_after_restore(
                error=str(e),
                recovery_point=recovery_point,
                cycle_id=cycle_id,
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
                extras={"reason": "synthesis_commit_failed"},
                completed_at_override=_completed_at_override,
            )
        except Exception as e:
            # The commit is part of the cycle contract. If it fails, the agent
            # response may exist, but replay must not treat the state update as
            # completed.
            error = f"Post-synthesis commit failed: {e}"
            logger.error("%s; transaction rolled back", error)
            return _fail_after_restore(
                error=error,
                recovery_point=recovery_point,
                cycle_id=cycle_id,
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
                extras={"reason": "post_synthesis_commit_failed"},
                completed_at_override=_completed_at_override,
            )

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
