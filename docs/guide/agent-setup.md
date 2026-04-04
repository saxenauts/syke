# Syke Setup

## For Humans

Paste this into your agent session:

```
Install and set up Syke by following:
https://raw.githubusercontent.com/saxenauts/syke/main/docs/guide/agent-setup.md
```

## For Agents

> Fetch this file with `curl -fsSL`, not WebFetch.

Syke is cross-harness memory for agents. It watches sessions across Claude Code, Codex, Cursor, Hermes, OpenCode, and Gemini, synthesizes a living context (MEMEX), and distributes it with a skill file to every connected harness. Background sync keeps it updated every ~15 minutes.

### 1. Install

```bash
pipx install syke   # or: uv tool install syke
```

Requires Python 3.12+.

### 2. Configure a provider

Ask the user which LLM provider they use and how they authenticate.

**OAuth providers** (GitHub Copilot, Google, OpenAI Codex):
```bash
syke auth login <provider> --use
```

**API key providers** (Anthropic, OpenAI, kimi-coding, openrouter):
```bash
syke auth set <provider> --api-key <KEY> --use
```

Anthropic does not reliably support OAuth for third-party tools. Use an API key.

Verify with `syke auth status`.

### 3. Run setup

```bash
syke setup --agent
```

Returns JSON. Read the `status` field:
- `"complete"` — done. Background ingestion is running.
- `"needs_provider"` — go back to step 2.
- `"failed"` — read the `error` field, fix, retry.

Do NOT run `syke setup` without `--agent` — that opens an interactive menu.

After setup, verify with `syke doctor`.

### 4. Tell the user

Syke is running. Background ingestion takes a few minutes. A skill file has been installed into each detected harness so agents already know how to use Syke. The user can start using `syke ask` and `syke record` immediately.

---

## Usage

Read the memex at the start of every session — it's the user's current work context across all harnesses:
```bash
syke context
```

Ask questions that span sessions and harnesses:
```bash
syke ask "what am I working on?"
syke ask "what are my open TODOs this week?"
```

Record decisions, completions, and findings so future sessions know:
```bash
syke record "DECISION: Using JWT for auth. Deleted session-cookie code."
syke record "TODO: Add rate limiting before release."
```

| Command | When |
|---------|------|
| `syke context` | Start of session. Read the memex. |
| `syke ask "..."` | Recall from past sessions across harnesses. |
| `syke record "..."` | After decisions, TODOs, learnings. Be specific. |
| `syke status` | Check sources, daemon, memex state. |
| `syke doctor` | Health check when something seems wrong. |
