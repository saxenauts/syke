"""Context-file distribution for downstream agent surfaces.

This module owns the file-level projections used outside the trusted Syke
runtime: exported memex files and Syke capability registration.
"""

from __future__ import annotations

import logging
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from syke.db import SykeDB

from syke.config import SKILLS_DIRS

log = logging.getLogger(__name__)
CURSOR_COMMANDS_DIR = Path.home() / ".cursor" / "commands"
COPILOT_AGENTS_DIR = Path.home() / ".copilot" / "agents"
ANTIGRAVITY_WORKFLOWS_DIR = Path.home() / ".gemini" / "antigravity" / "global_workflows"


def distribute_memex(db: SykeDB, user_id: str) -> Path | None:
    """Verify memex exists but do NOT overwrite the workspace file.

    The agent writes ~/.syke/MEMEX.md during synthesis. Distribution must not
    overwrite it — that caused a preamble-accumulation loop where each cycle
    added another copy of the onboarding header into the DB.

    Skill distribution (install_skill) handles delivery to harness dirs.
    Returns the workspace MEMEX path if content exists, None otherwise.
    """
    from syke.memory.memex import get_memex_for_injection
    from syke.runtime.workspace import MEMEX_PATH

    content = get_memex_for_injection(db, user_id)
    if not content or content.startswith("[First run") or content.startswith("[No "):
        return None

    # The workspace file is written by synthesis (_write_memex_artifact).
    # We only report its path for status display.
    return MEMEX_PATH if MEMEX_PATH.exists() else None


# --- Capability registration ---


def _get_skill_content() -> str:
    """Return the SKILL.md content.

    The package resource is the install-time source. The repo-root fallback
    keeps source checkouts usable if packaging data is unavailable.
    """
    try:
        return files("syke.distribution").joinpath("SKILL.md").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        from syke.config import PROJECT_ROOT

        skill_path = PROJECT_ROOT / "SKILL.md"
        if skill_path.exists():
            return skill_path.read_text(encoding="utf-8")
        raise


def _render_skill_content(user_id: str) -> str:
    return _get_skill_content().replace("{user}", user_id)


def _write_text_file(target: Path, content: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(target)
    return target


def _build_cursor_command_content(user_id: str) -> str:
    return (
        "# Syke\n\n"
        "Use Syke as your local memory layer. Start from `~/.syke/MEMEX.md`, "
        'then use `syke memex` for a fast read and `syke ask "..."` for deeper recall.\n\n'
        "When this command is used:\n"
        "1. Read the memex path above if it is accessible.\n"
        "2. Use `syke memex` when the current memex is enough.\n"
        "3. Use `syke ask` when you need deeper recall over the observed timeline.\n"
        "4. Use `syke record` after useful work.\n"
    )


def _build_copilot_agent_content(user_id: str) -> str:
    skill_body = _render_skill_content(user_id)
    return (
        "---\n"
        "name: Syke\n"
        "description: Use Syke local memory and the exported memex before starting work.\n"
        "---\n\n"
        f"{skill_body}"
    )


def _build_antigravity_workflow_content(user_id: str) -> str:
    return (
        "# Syke Workflow\n\n"
        "Use Syke as the stable local memory system for this workflow.\n\n"
        "- Memex path: `~/.syke/MEMEX.md`\n"
        "- Fast read: `syke memex`\n"
        '- Deep recall: `syke ask "..."`\n'
        '- Persist useful observations: `syke record "..."`\n'
        "- Health/debug: `syke status`, `syke doctor`\n"
    )


def install_skill(user_id: str) -> list[Path]:
    """Install Syke capability files to detected downstream agent surfaces.

    Installs the canonical `SKILL.md` package to configured skill directories and
    writes native capability wrappers for harnesses whose documented surface is
    commands/agents/workflows rather than direct skill folders.

    Returns list of paths where Syke capability files were installed.
    """
    content = _render_skill_content(user_id)
    installed: list[Path] = []

    for skills_dir in SKILLS_DIRS:
        tool_dir = skills_dir.parent
        if not tool_dir.exists():
            continue

        target = skills_dir / "syke" / "SKILL.md"
        try:
            installed.append(_write_text_file(target, content))
            log.debug("Installed skill to %s", target)
        except OSError as exc:
            log.warning("Failed to install skill to %s: %s", target, exc)

    wrapper_targets: list[tuple[Path, str]] = []
    if CURSOR_COMMANDS_DIR.parent.exists():
        wrapper_targets.append(
            (CURSOR_COMMANDS_DIR / "syke.md", _build_cursor_command_content(user_id))
        )
    if COPILOT_AGENTS_DIR.parent.exists():
        wrapper_targets.append(
            (COPILOT_AGENTS_DIR / "syke.agent.md", _build_copilot_agent_content(user_id))
        )
    if ANTIGRAVITY_WORKFLOWS_DIR.parent.exists():
        wrapper_targets.append(
            (ANTIGRAVITY_WORKFLOWS_DIR / "syke.md", _build_antigravity_workflow_content(user_id))
        )

    for target, wrapper_content in wrapper_targets:
        try:
            installed.append(_write_text_file(target, wrapper_content))
            log.debug("Installed capability wrapper to %s", target)
        except OSError as exc:
            log.warning("Failed to install capability wrapper to %s: %s", target, exc)

    return installed

