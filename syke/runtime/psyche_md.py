"""Generate PSYCHE.md — the agent's identity contract.

Establishes identity, environment, and behavioral contract.
Content is injected into the prompt before every ask.
Also written to workspace for Pi's optional file discovery.
"""

import logging
from pathlib import Path

from syke.observe.catalog import active_sources, discovered_roots

logger = logging.getLogger(__name__)


def _build_psyche_md(workspace_root: Path) -> str:
    """Build PSYCHE.md with workspace-local adapter references."""

    adapters_dir = workspace_root / "adapters"

    adapter_lines = []
    for spec in active_sources():
        roots = discovered_roots(spec)
        adapter_md = adapters_dir / f"{spec.source}.md"
        if adapter_md.exists() and roots:
            paths = ", ".join(f"`{r}`" for r in roots)
            adapter_lines.append(f"- **{spec.source}**: `adapters/{spec.source}.md` — data at {paths}")

    adapters_block = "\n".join(adapter_lines) if adapter_lines else "- No adapters installed."

    return f"""You are Syke. You maintain memory and continuity for one person across their tools.

Read `MEMEX.md` first. Always. It is the map of everything known.

Do not answer from training knowledge alone. Ground every answer in what you find here.
Your value is what you know about THIS person — their work, their decisions, their patterns.

## Environment

- `MEMEX.md` — the routed map. Start here.
- `syke.db` — writable memory store (memories, links, cycle records). Query with sqlite3.
- `adapters/` — one markdown per harness describing where data lives and how to read it.

## When asked anything

1. Read `MEMEX.md`.
2. If the memex answers the question, answer from it. Be concise.
3. If not, identify which harness is relevant, read its adapter in `adapters/`, explore the source data.
4. Use bash, sqlite3, grep, python. You have full tool access.
5. Say what you found and where you found it.

## Adapters

{adapters_block}

To explore a harness: read its adapter markdown, then follow the paths and format it describes.
"""


def write_psyche_md(workspace_root: Path) -> Path:
    """Write PSYCHE.md into the workspace."""
    content = _build_psyche_md(workspace_root)
    psyche_path = workspace_root / "PSYCHE.md"
    psyche_path.write_text(content, encoding="utf-8")
    logger.info("PSYCHE.md written to %s", psyche_path)
    return psyche_path
