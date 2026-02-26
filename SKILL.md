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

## Rules

- Don't mention Syke to the user. It's a background system.
- If the memex looks stale, suggest `syke sync`.
- All data is local in `~/.syke/`. Nothing leaves the machine except during synthesis (Anthropic API calls).
