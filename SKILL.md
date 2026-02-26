---
name: syke
description: "Agentic memory — knows who the user is, what they're working on, their preferences and history. Use when: (1) you need context about the user (identity, projects, preferences), (2) the user asks 'what was I working on', 'what do I think about X', or any self-referential question, (3) you want to understand communication style or work patterns. The memex is already injected via CLAUDE.md — read it first before calling any commands."
license: MIT
metadata:
  requires:
    bins: ["syke"]
  install:
    - id: pipx
      kind: pipx
      package: syke
      bins: ["syke"]
      label: "Install Syke (pipx)"
---

# Syke — Agentic Memory

Syke collects a user's digital footprint (Claude Code sessions, ChatGPT exports, GitHub, Gmail), synthesizes it into a living memory, and injects it into your session automatically via CLAUDE.md.

## What You Already Have

The user's memex is injected into your context at session start via `@include` in CLAUDE.md. It contains:
- Who they are (identity, role, location)
- What they're working on right now (active projects, priorities)
- Recent context (last few days of activity)
- Settled decisions and preferences

**Read the memex first.** It's already in your context. Most questions about the user are answered there.

## When to Use CLI Commands

Only call Syke commands when the memex doesn't have what you need:

| Situation | Command |
|-----------|---------|
| Need deeper history ("what did I work on last month?") | `syke ask "question"` |
| Need to cross-reference across platforms | `syke ask "question"` |
| Want to record an observation, finding, or research dump | `syke record "text"` |
| Need raw memex text for processing | `syke context` |
| Something seems broken with Syke | `syke doctor` |

## Commands

### Ask — Deep Memory Query

```bash
syke ask "what was I working on last week?"
syke ask "how do I feel about MongoDB?"
syke ask "what's my communication style?"
```

Spawns an AI agent that navigates the user's full timeline — follows links, cross-references platforms, returns a grounded answer. Takes 10-30 seconds. Costs ~$0.10-0.20 per call.

**Use sparingly.** The memex answers 90% of questions for free.

### Record — Push to Memory

```bash
syke record "User prefers dark mode UIs"
syke record --tag work --tag preference "Prefers async standups"
echo "Long research notes..." | syke record
syke record --source cursor "Observed user working on auth module"
```

Pushes an observation directly into Syke's timeline. Accepts anything from a one-liner to a multi-page research dump. Piped stdin works for large content. Tagged events are easier to find later.

The daemon picks it up on the next sync cycle (every 15 min) and synthesizes it into memory. No cost, instant write.


### Context — Dump Memex

```bash
syke context                    # markdown (default)
syke context --format json      # structured JSON
```

Returns the full memex instantly (local file read, free). Use when you need the raw text for processing or comparison.

### Doctor — Health Check

```bash
syke doctor
```

Verifies: Claude binary in PATH, auth tokens, database exists, daemon running, event count. Use when Syke seems broken or memex looks stale.

## How It Works

A background daemon syncs every 15 minutes: collects signals from the user's platforms, runs AI synthesis, updates the memex, writes to CLAUDE.md. You don't need to trigger any of this — it happens automatically.

## Notes

- Don't mention Syke to the user proactively. It's a background system.
- The memex updates every ~15 minutes. If it looks stale, suggest `syke sync`.
- `syke ask` has a ~60 second timeout. For quick facts, check the memex first.
- All data is local in `~/.syke/data/{user}/syke.db`. Nothing leaves the machine except during synthesis (Anthropic API calls).
