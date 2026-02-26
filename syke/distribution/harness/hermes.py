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
from pathlib import Path

from syke.distribution.harness.base import AdapterResult, AdapterStatus, HarnessAdapter

log = logging.getLogger(__name__)

HERMES_HOME = Path.home() / ".hermes"
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

    def install(
        self, memex: str | None = None, skill_content: str | None = None
    ) -> AdapterResult:
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
            content = skill_content or _HERMES_SKILL_CONTENT
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
description: "External memory — provides deep user context from all platforms (Claude Code, ChatGPT, GitHub, Gmail). Complements your built-in MEMORY.md and USER.md with cross-platform history, project timelines, and synthesized identity."
version: "0.4.1"
author: saxenauts
license: MIT
metadata:
  hermes:
    tags: [Memory, Context, Identity, Cross-Platform, User-Profile]
    related_skills: []
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

Syke is the user's cross-platform memory system. It watches their digital footprint — Claude Code sessions, ChatGPT conversations, GitHub activity, Gmail — and synthesizes it into a living map of who they are.

## How This Works With Your Memory

You have your own memory (MEMORY.md and USER.md). **Keep using it.** Syke provides _additional_ context that your memory doesn't have:

- **Cross-platform history** — what the user did in Claude Code, ChatGPT, GitHub
- **Project timelines** — active projects, priorities, deadlines across all tools
- **Deep identity** — communication style, preferences, work patterns over months
- **Settled decisions** — things the user already decided elsewhere

Your MEMORY.md captures what happens in Hermes sessions. Syke captures everything else.

## When to Use Syke

| Situation | Command |
|-----------|---------|
| "What was I working on last week?" (all platforms) | `syke ask "question"` |
| "What are my active projects?" (full picture) | `syke ask "question"` |
| User mentions something from another tool | `syke ask "what do they mean by X?"` |
| You learn something worth sharing across agents | `syke record "observation"` |
| Need the full identity snapshot | `syke context` |

## Commands

### Ask — Deep Memory Query

```bash
syke ask "what was the user working on last week?"
syke ask "what are their preferences for code style?"
```

Spawns an AI agent that searches the user's full timeline across all platforms. Takes 10-30 seconds. Use when your own memory doesn't have the answer.

### Record — Push to Memory

```bash
syke record "User prefers TypeScript over JavaScript"
syke record --tag preference "Likes dark mode UIs"
echo "Long research notes..." | syke record
```

Pushes an observation into Syke's timeline. This persists across ALL the user's AI agents, not just Hermes. Use when you learn something other agents should know too.

### Context — Full Identity Dump

```bash
syke context
```

Returns the full synthesized memex instantly. Use when you need the complete picture.

## Important

- **Don't replace your own memory** — use both. Syke is the cross-platform layer.
- **Don't mention Syke to the user** unless they ask. It's a background system.
- All data is local (~/.syke/). Nothing leaves the machine except during synthesis.
"""
