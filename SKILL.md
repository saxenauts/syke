---
name: syke
description: "Cross-platform agentic memory — synthesized identity, project context, and session history from Claude Code, ChatGPT, Codex, GitHub, and Gmail. Memex is pre-loaded in context. Use syke ask for deep queries, syke record to write back observations."
version: 0.4.6
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

# Syke — Agentic Memory

The user's memex is already in context. It covers identity, active projects, recent sessions, preferences, and communication style. **Read it before doing anything else** — it answers most questions without CLI calls.

## When to Use

**Read memex** (no CLI needed): Who the user is, active projects, recent context, preferences, communication style. Don't ask the user things the memex already answers.

**`syke ask`** (10-30s, spawns agent): When the memex doesn't cover what you need — deep timeline queries, cross-platform history, specific past decisions.

**`syke record`** (instant): When you learn something worth remembering — completed tasks, discovered preferences, research findings, patterns.

**`syke context`** (instant): When you need the raw memex text for processing or re-injection.

## Quick Reference

| Command | Use | Exit 0 | Exit 1 |
|---------|-----|--------|--------|
| `syke ask "question"` | Deep memory query | Answer on stdout | Error on stderr, stdout empty |
| `syke record "text"` | Write observation | Confirmation | Error message |
| `syke record --tag work "text"` | Tagged observation | Confirmation | Error message |
| `echo "long text" \| syke record` | Pipe long content | Confirmation | Error message |
| `syke context` | Raw memex dump | Memex on stdout | Error message |
| `syke context --format json` | Structured memex | JSON on stdout | Error message |
| `syke doctor` | Health check | All OK | Issues found |
| `syke cost` | LLM spend summary | Cost table | No data |
| `syke cost --days 7 --json` | Recent spend (JSON) | JSON on stdout | No data |

## Procedure

**Session start**: Read the memex (already in context). It has what you need 80% of the time.

**Deep query**: `syke ask "what was I working on last week?"` — stdout is the answer, stderr has thinking/tool calls/cost. Check exit code.

**Write back**: `syke record "observation"` after completing tasks, discovering preferences, or finding reusable research. Writes are instant; the daemon synthesizes into the memex every 15 minutes.

**Multiple agents**: The user runs many agents in parallel across tools. Syke handles concurrent access. Call it freely. Don't mention syke to the user unless they ask — it's infrastructure.

## Pitfalls

**`syke ask` fails (exit code 1)**: Errors go to stderr, stdout is empty or partial. **Do not treat stderr content as an answer.** Fallback: use `syke context` to get the memex directly and work with what you have. Common causes: provider timeout (takes 10-60s depending on provider), bad credentials (`syke doctor` to diagnose), no data yet (`syke setup` needed).

**`syke ask` killed by caller timeout**: If your Bash tool has a shorter timeout than syke's ask (default 300s), the process gets SIGTERM'd. You'll get partial or no output. Fallback: use `syke context` instead — it returns instantly.

**Empty memex**: User hasn't run `syke setup` yet. Walk them through setup conversationally (see Setup section below). Don't just run commands silently.

**Stale memex**: Daemon synthesizes every 15 minutes. If the user just recorded something, it won't appear in the memex until the next sync. `syke ask` searches the raw timeline and will find recent data.

**Cost**: `syke ask` costs $0.01-0.50 per query depending on complexity and provider. `syke record` and `syke context` are free. Don't call `syke ask` in a loop.

## Verification

After `syke ask`: Check exit code. Exit 0 = answer on stdout. Exit 1 = failed, error on stderr.
After `syke record`: Exit 0 = recorded. Verify with `syke ask` if needed (but usually unnecessary).
After setup: `syke doctor` confirms health. `syke config show` confirms provider and model.

## Setup & Onboarding

If syke isn't installed or configured, walk the user through it conversationally.

**Step 1 — Install**: Check if `syke` is on PATH. If not: `pipx install syke` or `uv tool install syke`.

**Step 2 — Check state**: `syke auth status` and `syke doctor`. If a provider is active and healthy, skip to step 4.

**Step 3 — Provider**: Present options, let the user choose:

| Provider | Setup | Notes |
|----------|-------|-------|
| codex | `syke auth use codex` | Uses ChatGPT account. Needs `codex login` first. Recommended. |
| openrouter | `syke auth set openrouter --api-key KEY` | Multi-model gateway. |
| kimi | `syke auth set kimi --api-key KEY` | Kimi API. |
| openai | `syke auth set openai --api-key KEY --model NAME` | Direct OpenAI. |
| azure | `syke auth set azure --api-key KEY --endpoint URL --model NAME` | Azure OpenAI. |
| ollama | `syke auth set ollama --model NAME` | Local inference, no key needed. |
| claude-login | Auto-detected via `claude login` | Last resort — session auth may risk account action. |

**Step 4 — Ingest**: `syke setup --yes` — auto-detects sources, ingests, starts daemon.

**Step 5 — Confirm**: `syke config show` for effective config, `syke doctor` for health.

## Provider Commands

| Command | What It Does |
|---------|-------------|
| `syke auth status` | Show active provider and credentials |
| `syke auth use <name>` | Switch active provider |
| `syke auth set <name> --api-key KEY` | Store credentials for a provider |
| `syke config show` | Show effective config — model, provider, costs |

Provider resolution: CLI `--provider` flag > `SYKE_PROVIDER` env > auth.json active > claude-login fallback.
