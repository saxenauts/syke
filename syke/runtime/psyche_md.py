"""Agent identity and prompt construction.

Three-block contract:
  <psyche>   — static identity + world definition + adapters. Never changes between cycles.
  <memex>    — dynamic map with temporal header. Agent reads its own prior work here.
  <synthesis>— the control space. Swapped per experiment condition.

PSYCHE is written once per workspace. MEMEX and synthesis are injected at runtime.
"""

import logging
from pathlib import Path

from syke.observe.catalog import active_sources, discovered_roots

logger = logging.getLogger(__name__)

SYNTHESIS_PATH = Path(__file__).parent.parent / "llm" / "backends" / "skills" / "pi_synthesis.md"


def _build_psyche_md(workspace_root: Path, *, home: Path | None = None) -> str:
    """Build the static <psyche> block: identity, world definition, adapters.

    This is stable — identical every cycle for a given workspace.
    Temporal context belongs in the <memex> block, not here.

    When `home` is provided (replay sandboxes), adapter path discovery is
    rooted there to prevent listing live ~/.codex / ~/.claude paths.
    """
    adapters_dir = workspace_root / "adapters"

    adapter_lines = []
    listed_sources: set[str] = set()
    for spec in active_sources():
        roots = discovered_roots(spec, home=home)
        adapter_md = adapters_dir / f"{spec.source}.md"
        if adapter_md.exists() and roots:
            paths = ", ".join(f"`{r}`" for r in roots)
            adapter_lines.append(f"- **{spec.source}**: `adapters/{spec.source}.md` — data at {paths}")
            listed_sources.add(spec.source)

    if adapters_dir.exists():
        for adapter_md in sorted(adapters_dir.glob("*.md")):
            source = adapter_md.stem
            if source in listed_sources:
                continue
            adapter_lines.append(
                f"- **{source}**: `adapters/{source}.md` — read for paths and format"
            )

    adapters_block = "\n".join(adapter_lines) if adapter_lines else "- No adapters installed."

    return f"""<psyche>
You are Syke. A memory process. Each cycle you boot from your last state.

<world>
Harness — an AI tool the person uses (Claude Code, Codex, Cursor, etc.). Generates a trace: sessions, tasks, conversations. Harnesses have no memory across runs — they query Syke to start warm.

Adapter — a file describing one harness: where its data lives, what format it uses, how to read it.

syke.db — your persistent store. Memories you extracted and committed. Synthesis records. Links.

MEMEX — your map. Other harnesses and agents read it for snapshot awareness of this person. You manage memories and keep MEMEX current for the agents that depend on it.

Ask — anything arriving in natural language from outside: question, instruction, declaration. Most commonly a harness querying before it places context for the person.
</world>

<adapters>
{adapters_block}
</adapters>
</psyche>"""


def _build_memex_block(
    db,
    user_id: str,
    *,
    context: str = "ask",
    now: str | None = None,
    last_synthesis: str | None = None,
    cycle: int | None = None,
) -> str:
    """Build the <memex> block: temporal header + map content."""
    content = ""
    try:
        from syke.memory.memex import get_memex_for_injection
        from syke.llm.backends.pi_synthesis import CHARS_PER_TOKEN, MEMEX_TOKEN_LIMIT

        raw = get_memex_for_injection(db, user_id, context=context)
        if raw and raw.strip():
            token_est = len(raw) // CHARS_PER_TOKEN
            fill_pct = min(100, round(token_est / MEMEX_TOKEN_LIMIT * 100))
            content = f"[{token_est:,} / {MEMEX_TOKEN_LIMIT:,} tokens · {fill_pct}%]\n\n{raw}"
    except Exception:
        pass

    temporal_parts = []
    if cycle is not None:
        temporal_parts.append(f"Cycle: #{cycle}")
    if now:
        temporal_parts.append(f"Now: {now}")
    if last_synthesis:
        temporal_parts.append(f"Last cycle: {last_synthesis}")
    temporal_line = " · ".join(temporal_parts) if temporal_parts else ""

    inner = "\n\n".join(filter(None, [temporal_line, content]))
    if not inner.strip():
        return ""
    return f"\n\n<memex>\n{inner}\n</memex>"


def build_prompt(
    workspace_root: Path,
    db=None,
    user_id: str | None = None,
    *,
    home: Path | None = None,
    context: str = "ask",
    synthesis_path: Path | None = None,
    # legacy param — accepted but ignored, use synthesis_path
    skill_path: Path | None = None,
    now: str | None = None,
    last_synthesis: str | None = None,
    cycle: int | None = None,
) -> str:
    """Assemble the full prompt: <psyche> + <memex> + <synthesis>.

    `psyche`    — static, built from workspace layout.
    `memex`     — dynamic map injected with temporal context.
    `synthesis` — the experimental control space, loaded from file.

    `home` scopes adapter path discovery for replay sandboxes.
    `context` is "ask" or "synthesis" (controls memex injection behaviour).
    `synthesis_path` overrides the default SYNTHESIS_PATH.
    `now`, `last_synthesis`, `cycle` go into the <memex> temporal header.
    """
    psyche = _build_psyche_md(workspace_root, home=home)

    memex = ""
    if db and user_id:
        memex = _build_memex_block(
            db, user_id,
            context=context,
            now=now,
            last_synthesis=last_synthesis,
            cycle=cycle,
        )

    _sp = synthesis_path or skill_path or SYNTHESIS_PATH
    synthesis = ""
    if _sp and _sp.exists():
        synthesis = f"\n\n<synthesis>\n{_sp.read_text(encoding='utf-8').strip()}\n</synthesis>"

    return f"{psyche}{memex}{synthesis}"


def write_psyche_md(workspace_root: Path, *, home: Path | None = None) -> Path:
    """Write PSYCHE.md into the workspace for Pi's optional file discovery.

    Writes only the static <psyche> block — no temporal context.
    `home` scopes adapter path discovery for replay sandboxes.
    """
    content = _build_psyche_md(workspace_root, home=home)
    psyche_path = workspace_root / "PSYCHE.md"
    psyche_path.write_text(content, encoding="utf-8")
    logger.info("PSYCHE.md written to %s", psyche_path)
    return psyche_path
