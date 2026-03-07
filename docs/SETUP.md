# Syke Setup Guide

Step-by-step setup for running Syke locally.

---

## Prerequisites

- Python 3.12+ (tested on 3.14)
- `pipx` or `uv` for installation
- LLM provider auth (see Authentication section below)

---

## Install

```bash
pipx install syke
syke setup --yes
```

That's it. Setup detects your data sources, ingests them, and starts the background daemon. Synthesis runs automatically on the daemon's first tick.

Alternative with uv:
```bash
uv tool install syke
syke setup --yes
```

### From Source (Development)

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
syke setup --yes
```

---

## Authentication

Syke supports multiple LLM providers. Setup shows a picker — choose whichever you have:

**Codex (ChatGPT Plus)** — local proxy translates Claude API to OpenAI Responses API:
```bash
syke auth use codex
# Reads token from ~/.codex/auth.json (created by codex CLI)
```

**OpenRouter** — API key auth:
```bash
syke auth set openrouter --api-key YOUR_OPENROUTER_KEY
```

**Zai** — API key auth:
```bash
syke auth set zai --api-key YOUR_ZAI_KEY
```

**Claude Code** — session auth, auto-detected if available:
```bash
claude login  # Requires Max/Team/Enterprise
```

**Switch providers**:
```bash
syke auth use codex              # Set active provider
syke auth status                 # Show current provider + credentials
SYKE_PROVIDER=openrouter syke ask "question"  # One-time override
```

**Provider resolution precedence**: CLI `--provider` flag > `SYKE_PROVIDER` env var > `~/.syke/auth.json` active_provider > auto-detect.

Auth stored at `~/.syke/auth.json`. Codex tokens read from `~/.codex/auth.json` (managed by codex CLI).

### Agent-driven setup

An AI agent can install Syke non-interactively by specifying the provider explicitly:

```bash
syke setup --provider codex --yes
```

Without `--provider`, setup prints a structured inventory to stdout (no auto-selection):
```
[ready]  claude-login  — Claude Code session auth
[ready]  codex  — ChatGPT Plus via Codex
[no key]  openrouter  — OpenRouter — enter API key
```

The agent reads this output, picks a provider, and re-runs with `--provider <id>`. `--yes` auto-consents to confirmations (daemon install) but never makes preference decisions.

---

## Platform Sources

### Claude Code (automatic)

Detected automatically during setup. Parses local JSONL session files.

### ChatGPT Export

1. Go to ChatGPT → Settings → Data Controls → Export Data
2. Wait for email with download link
3. Download the ZIP file
4. Run:

```bash
syke ingest chatgpt --file ~/Downloads/your-export.zip
```

### GitHub (with token for private repos)

1. Create a personal access token with `repo` and `read:user` scopes
2. Add to `~/.syke/.env`: `GITHUB_TOKEN=ghp_...`
3. Run:

```bash
syke ingest github --username YOUR_USERNAME
```

### Gmail

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project, enable Gmail API
3. Create OAuth 2.0 credentials (Desktop app)
4. Download `credentials.json` to `~/.config/syke/gmail_credentials.json`
5. Run:

```bash
syke ingest gmail
# First run opens browser for OAuth consent
```

---

## After Setup

```bash
# Check health
syke doctor

# View your memex
syke context

# Ask anything about yourself
syke ask "What did I work on last week?"

# Daemon runs every 15 min automatically — check status
syke daemon status
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | Reinstall: `pipx install --force syke` |
| Doctor shows `FAIL auth` | Set up a provider (see Authentication section) |
| Provider not found | Check `syke auth status` — verify credentials and active provider |
| Gmail says "credentials not found" | Download OAuth credentials from Google Cloud Console |
| GitHub returns 403 | Rate limited — add `GITHUB_TOKEN` to `~/.syke/.env` |
| Synthesis skipped | Need at least 5 events — the daemon will retry on the next sync cycle |

---

## File Locations

| What | Where |
|------|-------|
| User data | `~/.syke/data/{user_id}/` |
| SQLite database | `~/.syke/data/{user_id}/syke.db` |
| Memex context file | `~/.syke/data/{user_id}/CLAUDE.md` (auto-generated, injected into Claude Code) |
| Auth store | `~/.syke/auth.json` |
| Codex tokens (if using Codex) | `~/.codex/auth.json` |
| Daemon log | `~/.config/syke/daemon.log` |
| Daemon plist (macOS) | `~/Library/LaunchAgents/com.syke.daemon.plist` |
