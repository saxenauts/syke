"""Construct Syke's installed self-model and per-operation context.

Pi receives the stable ``syke_self`` resource as its system prompt. Each fresh
operation receives four separate sections: self-observation, MEMEX, Syke's
operating notes from ``workspace/OPERATING.md``, and the current operation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from syke.memory.learned import render_operating_notes_block
from syke.memory.memex_budget import MEMEX_TOKEN_LIMIT, measure_memex

logger = logging.getLogger(__name__)


def format_now_for_prompt(dt: datetime) -> str:
    """Format an authoritative reference time in the host timezone."""
    local_dt = dt.astimezone()
    tz_name = local_dt.tzname() or "local"
    offset = local_dt.utcoffset()
    offset_minutes = int(offset.total_seconds() / 60) if offset is not None else 0
    utc_sign = "+" if offset_minutes >= 0 else "-"
    utc_hours, utc_minutes = divmod(abs(offset_minutes), 60)
    utc_offset = f"UTC{utc_sign}{utc_hours}"
    if utc_minutes:
        utc_offset += f":{utc_minutes:02d}"
    return f"{local_dt.strftime('%Y-%m-%d %H:%M')} {tz_name} ({utc_offset})"


def _build_memex_block(
    db,
    user_id: str,
    *,
    context: str = "ask",
) -> str:
    """Render the current MEMEX under its size and change rules."""
    content = "No current MEMEX is available. Reconstruct only what this operation requires."
    tokens = 0
    try:
        from syke.memory.memex import get_memex_for_injection

        raw = get_memex_for_injection(db, user_id, context=context)
        if raw and raw.strip():
            content = raw.strip()
            tokens = int(measure_memex(content)["tokens"])
    except Exception:
        if context != "ask":
            # Synthesis must not be told to reconstruct MEMEX because a read
            # failed; that would invite rewriting accepted state. Fail closed.
            raise
        logger.warning("Unable to render the accepted MEMEX", exc_info=True)

    return f"""# MEMEX

Your map: {tokens:,} / {MEMEX_TOKEN_LIMIT:,} tokens. That is a hard limit; the host rejects a run \
that leaves it over. It points into your memories and their evidence, and how it is organized \
is yours. Change it when its meaning changes, not to advance a timestamp, mirror telemetry, or \
show activity. Where it matters for trust, note when the evidence was last checked.

{content}
"""


def _build_self_view_block(
    db,
    user_id: str,
    workspace_root: Path,
    session_dir: Path | None,
    cycle_runtime: Path | None,
    *,
    context: str,
    home: Path | None,
    selected_sources: tuple[str, ...] | None,
) -> str:
    """Build the host-established self-observation without blocking a cycle."""
    try:
        from syke.runtime.self_view import build_self_view

        return build_self_view(
            db,
            user_id,
            workspace_root=workspace_root,
            session_dir=session_dir,
            cycle_runtime=cycle_runtime,
            context=context,
            home=home,
            selected_sources=selected_sources,
        )
    except Exception:
        logger.warning("Unable to build Syke self-observation", exc_info=True)
        return """# Self-observation

Self-observation could not be built for this run. Treat Syke's current condition and
evidence routes as unknown rather than healthy.
"""


def _format_time_limit(time_limit_s: float | None) -> str:
    if time_limit_s is None or time_limit_s <= 0:
        return ""
    seconds = float(time_limit_s)
    shown = f"{int(seconds):,}" if seconds.is_integer() else f"{seconds:,.1f}"
    return f"Time limit: {shown} s. "


def _build_operation_block(
    *,
    context: str,
    operation_id: str | None,
    now: str,
    condition: str,
    incoming_records: str,
    answer_obligation: str | None,
    first_run_guidance: str,
    additional_guidance: str,
    time_limit_s: float | None = None,
) -> str:
    """Render what this run is, when it is, and how long it has.

    A replay renders exactly as a wake: nothing tells the agent it is a replay.
    """
    limit = _format_time_limit(time_limit_s)
    clock = f"- Now: {now}. Use this, not the system clock, for today and for relative dates."
    operation = operation_id or "unavailable"
    extra = (
        f"\n\n## Additional guidance\n\n{additional_guidance.strip()}"
        if additional_guidance.strip()
        else ""
    )

    if context == "ask":
        question = answer_obligation.strip() if answer_obligation else ""
        question_text = f"\n\nQuestion: {question}" if question else ""
        return f"""# This run

- Kind: ask
- Operation ID: {operation}
{clock}
- {limit}What you write during an ask stays; nothing is checked or rolled back.{question_text}

Someone is waiting. Answer from what you already know first: your MEMEX, your memories, and the \
pointers in them. Say where the deeper evidence is and what is uncertain. If it isn't in your \
memory, say so plainly and say where it would be. If one quick look settles it, look; go further \
into the sources only when the question needs it.{extra}
"""

    records = incoming_records.strip()
    records_text = f"\n\n{records}" if records else ""
    bootstrap = (
        f"\n\n## First run\n\n{first_run_guidance.strip()}" if first_run_guidance.strip() else ""
    )
    return f"""# This run

- Kind: wake
- Operation ID: {operation}
{clock}
- {limit}If the run times out, fails, or is rejected, graph and MEMEX changes go back to where \
they were when this run started. Files in your workspace, including OPERATING.md, and in this \
run's folder stay, so write a lesson down when you learn it.
- Condition: {condition}

No one is waiting. The host doesn't track what changed in your sources, so an empty list of \
records doesn't mean nothing changed. Bring your picture up to date with what changed since your \
last run, and keep what you learned doing it. If you are blocked or run out of time, write down \
the blocker and where the evidence is so the next run can pick it up.{records_text}{bootstrap}{extra}
"""


def build_prompt(
    workspace_root: Path,
    db=None,
    user_id: str | None = None,
    *,
    now: str,
    home: Path | None = None,
    context: str = "ask",
    synthesis_path: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
    session_dir: Path | None = None,
    cycle_runtime: Path | None = None,
    include_memex: bool = True,
    include_self_view: bool = True,
    operation_id: str | None = None,
    condition: str = "ordinary",
    incoming_records: str = "",
    answer_obligation: str | None = None,
    first_run_guidance: str = "",
    time_limit_s: float | None = None,
) -> str:
    """Assemble the dynamic sections supplied to a fresh Syke operation.

    ``answer_obligation`` is the ask's question. ``time_limit_s`` is the run's
    wall-clock limit, computed by the caller before the prompt is built.
    Replay callers may supply ``synthesis_path`` as additional operation
    guidance. The run block itself is always present.
    """
    blocks: list[str] = []
    if include_self_view and db is not None and user_id:
        blocks.append(
            _build_self_view_block(
                db,
                user_id,
                workspace_root,
                session_dir,
                cycle_runtime,
                context=context,
                home=home,
                selected_sources=selected_sources,
            )
        )
    elif include_self_view:
        blocks.append(
            """# Self-observation

Self-observation is unavailable because no bound graph and person were supplied to this
prompt construction.
"""
        )

    if include_memex and db is not None and user_id:
        blocks.append(_build_memex_block(db, user_id, context=context))

    if db is not None and user_id:
        blocks.append(render_operating_notes_block(workspace_root))

    guidance = ""
    if synthesis_path is not None and synthesis_path.exists():
        guidance = synthesis_path.read_text(encoding="utf-8").strip()
    blocks.append(
        _build_operation_block(
            context=context,
            operation_id=operation_id,
            now=now,
            condition=condition,
            incoming_records=incoming_records,
            answer_obligation=answer_obligation,
            first_run_guidance=first_run_guidance,
            additional_guidance=guidance,
            time_limit_s=time_limit_s,
        )
    )

    return "\n\n".join(block for block in blocks if block.strip())
