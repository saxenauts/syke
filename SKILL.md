---
name: syke
description: "The user's cross-platform agentic memory — a complete, synthesized picture of who they are, what they're working on, and how they think. Connected to their other AI tools, sessions, and platforms. The memex is already loaded in context and covers identity, active projects, recent sessions, preferences, and communication patterns. Anything about the user — their history, opinions, habits, past decisions, what they were doing last week — is either in the memex already or reachable via syke ask. Write back observations, discoveries, and completions proactively so the next session inherits this one's context."
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

The user's memex is already in context. It contains identity, active projects, recent activity, preferences, and communication style. **Read it first** — it answers most questions without any CLI calls.

## When to Read the Memex

At session start, before doing anything else. The memex tells you:
- Who the user is and how they communicate
- What projects are active and their current state
- Recent context (what happened in the last few sessions)
- Preferences and patterns you should respect

Don't ask the user things the memex already answers.

## When to Write Back

Record observations proactively — don't wait to be asked:

```
syke record "User prefers dark mode UIs"
syke record --tag work "Switched team to async standups"
echo "Long research notes..." | syke record
```

**Write when you:**
- Complete a task or milestone — record what was done and outcome
- Discover a user preference or correction — "actually I prefer X"
- Find something useful during research — save it for future sessions
- Notice a pattern — recurring topic, workflow habit, tool preference
- Learn something the next session should know — blockers, decisions, context

Writes are instant. The daemon synthesizes them into the memex every 15 minutes.

## When to Go Deeper

Use `syke ask` only when the memex doesn't have what you need:

```
syke ask "what was I working on last week?"
syke ask "how do I feel about MongoDB?"
syke ask "what did the client say about the deadline?"
```

This spawns an AI agent that navigates the full timeline — follows links, cross-references platforms. Takes 10-30s. **Use sparingly.**

## Other Commands

| Command | Use |
|---------|-----|
| `syke context` | Get raw memex text for processing |
| `syke doctor` | Verify auth, daemon, database health |

## Setup & Onboarding (for agents)

If syke isn't installed or configured, walk the user through it conversationally. Don't just run commands silently — explain what you're doing and let them choose.

### Step 1: Install (if needed)

Check if `syke` is on PATH. If not:
```bash
pipx install syke    # or: uv tool install syke
```

### Step 2: Check current state

```bash
syke auth status     # what's configured, what's active
syke doctor          # full health check
```

Show the user what you find. If a provider is already active and healthy, skip to step 4.

### Step 3: Provider setup

Present the options to the user and let them choose. Explain tradeoffs:

| Provider | How to set up | What to tell the user |
|----------|--------------|----------------------|
| codex | `syke auth use codex` (needs `codex login` first) | Uses their ChatGPT Plus subscription. Easiest if they have it. Recommended. |
| openrouter | `syke auth set openrouter --api-key KEY` | Multi-model gateway. User needs an API key from openrouter.ai. |
| zai | `syke auth set zai --api-key KEY` | z.ai API key. |
| kimi | `syke auth set kimi --api-key KEY` | Kimi API key. |
| azure | `syke auth set azure --api-key KEY --endpoint URL --model NAME` | Azure OpenAI deployment. User needs endpoint URL, model name, and key. |
| openai | `syke auth set openai --api-key KEY --model NAME` | Direct OpenAI API. User needs key and model name. |
| ollama | `syke auth set ollama --model NAME` | Local inference, no API key needed. Ask which model they have. |
| claude-login | Auto-detected if `claude login` was run | Uses their personal Anthropic login. Warn: session auth not designed for background use — may risk account action. Last resort. |

After the user picks, run the appropriate `syke auth set` or `syke auth use` command. Confirm with `syke auth status`.

### Step 4: Ingest and start

```bash
syke setup --yes    # auto-detect sources, ingest, start daemon
```

The `--yes` flag consents to daemon install but doesn't override the provider they just chose. Setup auto-detects Claude Code sessions, Codex sessions, ChatGPT exports, and GitHub — no user input needed.

### Step 5: Confirm

```bash
syke config show    # show effective config — provider, model, costs per task
syke doctor         # verify everything is healthy
```

Show the user what provider is active, what model is running, and what each operation costs. Synthesis runs on the daemon's first tick (within 15 minutes).

## Provider Commands

| Command | What It Does |
|---------|-------------|
| `syke auth status` | Show active provider, credentials, routing |
| `syke auth use <name>` | Switch active provider |
| `syke auth set <name> --api-key KEY` | Store API key for a provider |
| `syke config show` | Show effective config — model, provider, costs |

Provider resolution: CLI `--provider` flag > `SYKE_PROVIDER` env > auth.json active > claude-login fallback.

After `syke ask` and `syke sync`, cost is displayed (provider, duration, USD, tokens).

## How Syke Works in Practice

Syke runs in the background — syncing, synthesizing, updating the memex every 15 minutes. The user doesn't have to actively manage it. But you (and every other agent the user runs) should actively use it:

- **Read the memex** at session start — it's your context about the user
- **Write back** with `syke record` when you learn something worth remembering
- **Ask deeper** with `syke ask` when the memex doesn't cover what you need

The user may be running 10 agents in parallel across different tools. Syke is stable under concurrent access — call it freely. The user can interact with it directly if they want (`syke ask`, `syke status`, `syke config show`), but they don't have to. Their agents handle it.

All data is local in `~/.syke/`. Nothing leaves the machine except LLM API calls to the configured provider during synthesis.
