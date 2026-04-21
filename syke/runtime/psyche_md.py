"""Agent identity and prompt construction.

Four-block contract:
  <psyche>   — static identity + world definition + adapters. Never changes between cycles.
  <now>      — authoritative current time. Required every call. Carries the one rule:
               resolve relative time against the as-of; ignore host clock/mtimes.
  <memex>    — dynamic map content. Agent reads its own prior work here.
  <synthesis>— the control space. Swapped per experiment condition.

PSYCHE is written once per workspace. <now>, <memex>, and <synthesis> are injected at runtime.
"""

import logging
import time as _time
from datetime import datetime
from pathlib import Path

from syke.observe.catalog import active_sources, discovered_roots

logger = logging.getLogger(__name__)

SYNTHESIS_PATH = Path(__file__).parent.parent / "llm" / "backends" / "skills" / "pi_synthesis.md"


def format_now_for_prompt(dt: datetime) -> str:
    """Format a datetime for the <now> block. Host-timezone stamped.

    Accepts naive or tz-aware. Shared across production and replay so the
    time string shape is identical regardless of caller.
    """
    tz_name = _time.tzname[_time.daylight] if _time.daylight else _time.tzname[0]
    offset = -_time.timezone if not _time.daylight else -_time.altzone
    utc_sign = "+" if offset >= 0 else "-"
    utc_hours = abs(offset) // 3600
    return f"{dt.strftime('%Y-%m-%d %H:%M')} {tz_name} (UTC{utc_sign}{utc_hours})"


def _build_now_block(
    now: str,
    *,
    cycle: int | None = None,
    last_synthesis: str | None = None,
    directive: bool = True,
) -> str:
    """Build the <now> block: as-of time + optional cycle line + directive.

    `now` is a pre-formatted string (see `format_now_for_prompt`) so callers
    can thread in wall-clock, simulated-cycle, or probe-cutoff time with
    identical shape.
    """
    lines = [f"As of: {now}"]

    cycle_parts = []
    if cycle is not None:
        cycle_parts.append(f"Cycle #{cycle}")
    if last_synthesis:
        cycle_parts.append(f"Last cycle: {last_synthesis}")
    if cycle_parts:
        lines.append(" · ".join(cycle_parts))

    if directive:
        lines.append("Resolve today/yesterday/last/now/most-recent against this as-of.")
        lines.append("Ignore host `date`, file mtimes, and system clock as sources of truth.")

    body = "\n".join(lines)
    return f"\n\n<now>\n{body}\n</now>"


def _build_psyche_md(
    workspace_root: Path,
    *,
    home: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
) -> str:
    """Build the static <psyche> block: identity, world definition, adapters.

    This is stable — identical every cycle for a given workspace.
    Temporal context belongs in the <memex> block, not here.

    When `home` is provided (replay sandboxes), adapter path discovery is
    rooted there to prevent listing live ~/.codex / ~/.claude paths.
    """
    adapters_dir = workspace_root / "adapters"
    selected_set = set(selected_sources) if selected_sources is not None else None

    adapter_lines = []
    listed_sources: set[str] = set()
    for spec in active_sources():
        if selected_set is not None and spec.source not in selected_set:
            continue
        roots = discovered_roots(spec, home=home)
        adapter_md = adapters_dir / f"{spec.source}.md"
        if adapter_md.exists() and roots:
            paths = ", ".join(f"`{r}`" for r in roots)
            adapter_lines.append(
                f"- **{spec.source}**: `adapters/{spec.source}.md` — data at {paths}"
            )
            listed_sources.add(spec.source)

    if adapters_dir.exists():
        for adapter_md in sorted(adapters_dir.glob("*.md")):
            source = adapter_md.stem
            if selected_set is not None and source not in selected_set:
                continue
            if source in listed_sources:
                continue
            adapter_lines.append(
                f"- **{source}**: `adapters/{source}.md` — read for paths and format"
            )

    adapters_block = "\n".join(adapter_lines) if adapter_lines else "- No adapters installed."

    return f"""<psyche>
You are Syke. A memory process. Each cycle you boot from your last state.

<world>
Harness — an AI tool the person uses. Generates a trace: sessions, tasks, conversations.
Harnesses have no memory across runs — they query Syke to start warm.

Adapter — a file describing one harness: where its data lives, what format it uses, how to read it.

syke.db — your persistent store. Memories you extracted and committed. Links.

MEMEX — your map. Harnesses and agents read it before they place context for the person.
What you keep here is what they navigate by.

Ask — anything arriving: question, request, reminder, cycle trigger.
</world>

<principles>
Schema: memories has freeform content — no title, status, or kind.
links uses source_id and target_id.

Start cheap: counts, recent titles, active memories, links. Drill only where evidence looks durable.
If a query fails, correct it to actual schema — never invent fields.

Continuity is the default. Revise existing memories before creating new ones.
A memory is a strand of work, state, or decision that would still matter
in a future cycle — not every observation.
When evidence is ambiguous, preserve optionality. Don't collapse or split early.
Links are sparse: only when two memories have a durable relation that matters later.

MEMEX is a projection over durable state — not the place to carry structure forward in prose.
If a route keeps growing, materialize the structure in syke.db first,
then project the simpler map into MEMEX.
If MEMEX is absent, bootstrap it from current active memories.
</principles>

<adapters>
{adapters_block}
</adapters>
</psyche>"""


def _build_memex_block(
    db,
    user_id: str,
    *,
    context: str = "ask",
) -> str:
    """Build the <memex> block: map content only. Time lives in <now>."""
    content = ""
    try:
        from syke.llm.backends.pi_synthesis import CHARS_PER_TOKEN, MEMEX_TOKEN_LIMIT
        from syke.memory.memex import get_memex_for_injection

        raw = get_memex_for_injection(db, user_id, context=context)
        if raw and raw.strip():
            token_est = len(raw) // CHARS_PER_TOKEN
            fill_pct = min(100, round(token_est / MEMEX_TOKEN_LIMIT * 100))
            content = f"[{token_est:,} / {MEMEX_TOKEN_LIMIT:,} tokens · {fill_pct}%]\n\n{raw}"
    except Exception:
        pass

    if not content.strip():
        return ""
    return f"\n\n<memex>\n{content}\n</memex>"


def build_prompt(
    workspace_root: Path,
    db=None,
    user_id: str | None = None,
    *,
    now: str,
    home: Path | None = None,
    context: str = "ask",
    synthesis_path: Path | None = None,
    last_synthesis: str | None = None,
    cycle: int | None = None,
    selected_sources: tuple[str, ...] | None = None,
    include_memex: bool = True,
    include_synthesis: bool = True,
    time_directive: bool = True,
) -> str:
    """Assemble the full prompt: <psyche> + <now> + <memex> + <synthesis>.

    `now` is REQUIRED. It is the one authoritative time surface. Callers
    decide the authority:
      - production ask: wall clock
      - production/replay synthesis: now_override or wall clock
      - replay-lab ask: probe reference cutoff

    `include_memex=False` drops the <memex> block (pure mode).
    `include_synthesis=False` drops the <synthesis> block (pure/zero modes).

    `home` scopes adapter path discovery for replay sandboxes.
    `context` is "ask" or "synthesis" (controls memex injection behaviour).
    `synthesis_path` overrides the default SYNTHESIS_PATH.
    `selected_sources` scopes adapter references in <psyche>.
    """
    psyche = _build_psyche_md(
        workspace_root,
        home=home,
        selected_sources=selected_sources,
    )
    now_block = _build_now_block(
        now,
        cycle=cycle,
        last_synthesis=last_synthesis,
        directive=time_directive,
    )

    memex = ""
    if include_memex and db and user_id:
        memex = _build_memex_block(db, user_id, context=context)

    synthesis = ""
    if include_synthesis:
        _sp = synthesis_path or SYNTHESIS_PATH
        if _sp and _sp.exists():
            synthesis = f"\n\n<synthesis>\n{_sp.read_text(encoding='utf-8').strip()}\n</synthesis>"

    return f"{psyche}{now_block}{memex}{synthesis}"


def write_psyche_md(
    workspace_root: Path,
    *,
    home: Path | None = None,
    selected_sources: tuple[str, ...] | None = None,
) -> Path:
    """Write PSYCHE.md into the workspace for Pi's optional file discovery.

    Writes only the static <psyche> block — no temporal context.
    `home` scopes adapter path discovery for replay sandboxes.
    `selected_sources` limits which adapter entries appear in the emitted psyche.
    """
    content = _build_psyche_md(
        workspace_root,
        home=home,
        selected_sources=selected_sources,
    )
    psyche_path = workspace_root / "PSYCHE.md"
    psyche_path.write_text(content, encoding="utf-8")
    logger.info("PSYCHE.md written to %s", psyche_path)
    return psyche_path
