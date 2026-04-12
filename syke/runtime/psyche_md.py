"""Agent identity and prompt construction.

PSYCHE is the agent's identity: who it is, what environment it has,
which adapters are available. Combined with MEMEX (the map) and the
skill prompt (how to reason), it forms the complete injected context
for both ask and synthesis.

The ecosystem pattern: inject everything into the prompt so the agent
starts with full context. File reads are only for going deeper.
"""

import logging
from pathlib import Path

from syke.observe.catalog import active_sources, discovered_roots

logger = logging.getLogger(__name__)

SKILL_PATH = Path(__file__).parent.parent / "llm" / "backends" / "skills" / "pi_synthesis.md"


def _build_psyche_md(workspace_root: Path, *, home: Path | None = None) -> str:
    """Build PSYCHE identity content with adapter references.

    When `home` is provided (e.g. for replay sandboxes), adapter path
    discovery is rooted at that directory instead of the real user home.
    This prevents the replay agent from listing paths under ``~/.codex``
    or ``~/.claude`` that belong to the live system.
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

    # Fallback: any adapter markdown on disk but not discoverable via the
    # host-style catalog (e.g. replay sandbox uses `harnesses/<name>/` layout,
    # not `~/.codex`). The markdown itself carries the paths — list it so the
    # agent knows it exists.
    if adapters_dir.exists():
        for adapter_md in sorted(adapters_dir.glob("*.md")):
            source = adapter_md.stem
            if source in listed_sources:
                continue
            adapter_lines.append(
                f"- **{source}**: `adapters/{source}.md` — read for paths and format"
            )

    adapters_block = "\n".join(adapter_lines) if adapter_lines else "- No adapters installed."

    return f"""You are Syke. You maintain memory and continuity for one person across their tools.

The MEMEX below is your current map. To go deeper, use adapters and bash/sqlite3.

Do not answer from training knowledge alone. Ground every answer in what you find here.
Your value is what you know about THIS person — their work, their decisions, their patterns.

## Environment

- `syke.db` — writable memory store (memories, links, cycle records). Query with sqlite3.
- `adapters/` — one markdown per harness describing where data lives and how to read it.

## Adapters

{adapters_block}

To explore a harness: read its adapter markdown, then follow the paths and format it describes.
"""


def build_prompt(
    workspace_root: Path,
    db=None,
    user_id: str | None = None,
    *,
    home: Path | None = None,
    context: str = "ask",
) -> str:
    """Build the complete injected prompt: PSYCHE + MEMEX + skill.

    Both ask and synthesis use this. The agent starts with full context —
    identity, knowledge map, and reasoning principles — without reading files.

    `home` is optional and scopes adapter path discovery for replay sandboxes.
    `context` is "ask" (default) or "synthesis"; synthesis suppresses the
    user-facing empty-memex placeholder so the agent builds from scratch.
    """
    psyche = _build_psyche_md(workspace_root, home=home)

    # Inject MEMEX content with fill bar so the agent sees budget pressure
    # in the same attention window as the content it's deciding about.
    memex = ""
    if db and user_id:
        try:
            from syke.memory.memex import get_memex_for_injection

            content = get_memex_for_injection(db, user_id, context=context)
            if content and content.strip():
                from syke.llm.backends.pi_synthesis import CHARS_PER_TOKEN, MEMEX_TOKEN_LIMIT

                token_est = len(content) // CHARS_PER_TOKEN
                fill_pct = min(100, round(token_est / MEMEX_TOKEN_LIMIT * 100))
                fill_bar = f"# MEMEX [{token_est:,} / {MEMEX_TOKEN_LIMIT:,} tokens · {fill_pct}%]"
                memex = f"\n---\n\n{fill_bar}\n\n{content}"
        except Exception:
            pass  # DB may not support memex queries (tests, minimal contexts)

    # Skill prompt (shared reasoning principles for both ask and synthesis)
    skill = ""
    if SKILL_PATH.exists():
        skill = SKILL_PATH.read_text(encoding="utf-8")

    return f"{psyche}{memex}\n\n---\n\n{skill}"


def write_psyche_md(workspace_root: Path, *, home: Path | None = None) -> Path:
    """Write PSYCHE.md into the workspace (for Pi's optional file discovery).

    `home` is optional and scopes adapter path discovery; pass the replay
    workspace root to prevent PSYCHE from listing live ~/.codex / ~/.claude
    paths during replay runs.
    """
    content = _build_psyche_md(workspace_root, home=home)
    psyche_path = workspace_root / "PSYCHE.md"
    psyche_path.write_text(content, encoding="utf-8")
    logger.info("PSYCHE.md written to %s", psyche_path)
    return psyche_path
