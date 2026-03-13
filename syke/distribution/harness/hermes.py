"""Hermes Agent harness adapter — A/B test Syke alongside native memory.

Hermes has its own memory system (MEMORY.md + USER.md) plus a persona
file (SOUL.md). This adapter installs Syke as a *skill* that teaches
Hermes about syke ask/record/context commands. Native memory is left
untouched — the user compares Hermes-only vs Hermes+Syke organically.

Hermes skill structure:
  ~/.hermes/skills/{category}/{skill-name}/SKILL.md
  ~/.hermes/skills/{category}/DESCRIPTION.md  (category descriptor)

We install to:
  ~/.hermes/skills/memory/DESCRIPTION.md
  ~/.hermes/skills/memory/syke/SKILL.md
"""

from __future__ import annotations

import logging

from syke.config import HERMES_HOME
from syke.distribution.harness.base import AdapterResult, AdapterStatus, HarnessAdapter

log = logging.getLogger(__name__)
HERMES_SKILLS = HERMES_HOME / "skills"
SYKE_CATEGORY = HERMES_SKILLS / "memory"
SYKE_SKILL_DIR = SYKE_CATEGORY / "syke"
SYKE_SKILL_PATH = SYKE_SKILL_DIR / "SKILL.md"
CATEGORY_DESC_PATH = SYKE_CATEGORY / "DESCRIPTION.md"


class HermesAdapter(HarnessAdapter):
    name = "hermes"
    display_name = "Hermes Agent"
    protocol = "agentskills"
    protocol_version = "1.0"
    has_native_memory = True

    def detect(self) -> bool:
        """Hermes is installed if ~/.hermes/config.yaml exists."""
        return HERMES_HOME.exists() and (HERMES_HOME / "config.yaml").exists()

    def install(self, memex: str | None = None, skill_content: str | None = None) -> AdapterResult:
        """Install Syke skill into Hermes. A/B mode: native memory untouched."""
        result = AdapterResult()

        if not self.detect():
            result.skipped.append("Hermes not installed")
            return result

        # 1. Category descriptor
        try:
            SYKE_CATEGORY.mkdir(parents=True, exist_ok=True)
            CATEGORY_DESC_PATH.write_text(_CATEGORY_DESCRIPTION)
            result.installed.append(CATEGORY_DESC_PATH)
            log.info("Wrote Hermes category descriptor: %s", CATEGORY_DESC_PATH)
        except OSError as e:
            result.warnings.append(f"Category descriptor: {e}")

        # 2. Syke SKILL.md (Hermes-flavored, A/B test framing)
        try:
            SYKE_SKILL_DIR.mkdir(parents=True, exist_ok=True)
            from syke import __version__

            content = skill_content or _HERMES_SKILL_CONTENT.replace("{version}", __version__)
            SYKE_SKILL_PATH.write_text(content)
            result.installed.append(SYKE_SKILL_PATH)
            log.info("Installed Hermes skill: %s", SYKE_SKILL_PATH)
        except OSError as e:
            result.warnings.append(f"SKILL.md install: {e}")

        return result

    def status(self) -> AdapterStatus:
        """Check if Hermes is installed and Syke is integrated."""
        detected = self.detect()
        connected = SYKE_SKILL_PATH.exists() if detected else False

        files = []
        if connected:
            files = [p for p in [CATEGORY_DESC_PATH, SYKE_SKILL_PATH] if p.exists()]

        # Report native memory state for A/B comparison
        notes_parts = []
        if detected:
            mem_path = HERMES_HOME / "memories" / "MEMORY.md"
            user_path = HERMES_HOME / "memories" / "USER.md"
            if mem_path.exists():
                size = len(mem_path.read_text())
                notes_parts.append(f"MEMORY.md: {size} chars")
            if user_path.exists():
                size = len(user_path.read_text())
                notes_parts.append(f"USER.md: {size} chars")

        return AdapterStatus(
            name=self.name,
            detected=detected,
            connected=connected,
            native_memory=self.has_native_memory,
            files=files,
            notes="; ".join(notes_parts) if notes_parts else "",
        )

    def uninstall(self) -> bool:
        """Remove Syke skill from Hermes. Does NOT touch native memory."""
        try:
            if SYKE_SKILL_PATH.exists():
                SYKE_SKILL_PATH.unlink()
            if SYKE_SKILL_DIR.exists():
                # Remove dir only if empty (we only put SKILL.md there)
                try:
                    SYKE_SKILL_DIR.rmdir()
                except OSError:
                    pass  # dir not empty — leave it
            # Don't remove category dir — other skills may live there
            log.info("Uninstalled Hermes skill")
            return True
        except OSError as e:
            log.warning("Hermes uninstall failed: %s", e)
            return False


# ---------------------------------------------------------------------------
# Static content
# ---------------------------------------------------------------------------

_CATEGORY_DESCRIPTION = """\
---
description: Memory and context skills — external knowledge sources for richer user understanding.
---
"""

_HERMES_SKILL_CONTENT = """\
---
name: syke
description: "Cross-platform agentic memory — synthesized identity, project context, and session history from Claude Code, ChatGPT, Codex, GitHub, and Gmail. Complements MEMORY.md and USER.md with cross-platform history and deep timeline queries."
version: "{version}"
author: saxenauts
license: MIT
metadata:
  hermes:
    tags: [Memory, Context, Identity, Cross-Platform, Agentic-Memory]
    related_skills: []
    requires_toolsets: [terminal]
  requires:
    bins: ["syke"]
  install:
    - id: pipx
      kind: pipx
      package: syke
      bins: ["syke"]
      label: "Install Syke (pipx)"
---

# Syke — External Memory for Hermes

Syke is the user's cross-platform memory. It watches their digital footprint — Claude Code sessions, ChatGPT conversations, Codex sessions, GitHub activity — and synthesizes it into a living identity map.

## When to Use

You have your own memory (MEMORY.md and USER.md). **Keep using it.** Syke provides what your memory doesn't: cross-platform history, project timelines across tools, deep identity patterns, and decisions made in other agents.

| Situation | Command |
|-----------|---------|
| Deep cross-platform query | `syke ask "what was I working on last week?"` |
| Full identity snapshot | `syke context` |
| Share learning across agents | `syke record "observation"` |
| Tagged observation | `syke record --tag work "text"` |

## Quick Reference

| Command | stdout | stderr | Exit 0 | Exit 1 |
|---------|--------|--------|--------|--------|
| `syke ask "q"` | Answer text | Thinking, tool calls, cost | Success | Failed — error on stderr |
| `syke context` | Raw memex | — | Success | No memex yet |
| `syke record "text"` | Confirmation | — | Recorded | Error |
| `syke cost --json` | JSON cost data | — | Success | No data |

## Procedure

**Cross-platform query**: `syke ask "question"` — takes 10-60s. Spawns an agent that navigates the full timeline. Use when your own MEMORY.md doesn't have the answer.

**Write back**: `syke record "observation"` — instant. Persists across ALL the user's agents, not just Hermes. Use when you learn something other agents should know.

**Full context**: `syke context` — instant. Returns the synthesized memex for processing.

## Pitfalls

**`syke ask` fails (exit code 1)**: Errors go to stderr only. Do not treat stderr as the answer. **Fallback**: use `syke context` to get the memex and work with what you have. Common causes: provider timeout, bad credentials (`syke doctor`), no data (`syke setup` needed).

**`syke ask` killed by timeout**: If your process tool has a shorter timeout than syke's default (300s), ask gets SIGTERM'd with no output. **Fallback**: `syke context` returns instantly.

**Stale data**: Daemon synthesizes every 15 minutes. Recent `syke record` writes won't appear in `syke context` until next sync, but `syke ask` searches the raw timeline and finds recent data.

**Cost**: `syke ask` costs $0.01-0.50 per query. `syke record` and `syke context` are free. Don't loop `syke ask`.

## Verification

After `syke ask`: exit code 0 = answer on stdout. Exit code 1 = failed, check stderr.
After `syke record`: exit code 0 = recorded.
Health check: `syke doctor` for full diagnostics.

Don't replace your own memory — use both. Don't mention syke to the user unless they ask. All data is local (~/.syke/).
"""
