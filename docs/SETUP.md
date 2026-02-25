# Syke Setup Guide

Step-by-step setup for running Syke locally.

---

## Prerequisites

- Python 3.12+ (tested on 3.14)
- `pipx` or `uv` for installation
- For memory synthesis: `claude login` (Claude Code Max/Team/Enterprise)

---

## Install

```bash
pipx install syke
syke setup --yes
```

That's it. Setup auto-detects your username, finds local data sources (Claude Code sessions, ChatGPT exports), runs synthesis, configures CLAUDE.md injection, and starts the daemon.

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

Syke uses Claude Code session auth — no API key needed.

```bash
claude login
```

Works with Max, Team, or Enterprise plans. Without auth, synthesis will fail.

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
| Doctor shows `FAIL auth` | Run `claude login` (Max/Team/Enterprise) |
| Gmail says "credentials not found" | Download OAuth credentials from Google Cloud Console |
| GitHub returns 403 | Rate limited — add `GITHUB_TOKEN` to `~/.syke/.env` |
| Synthesis skipped | Need at least 5 events — run `syke sync` after ingesting data |

---

## File Locations

| What | Where |
|------|-------|
| User data | `~/.syke/data/{user_id}/` |
| SQLite database | `~/.syke/data/{user_id}/syke.db` |
| CLAUDE.md (memex) | `~/.syke/data/{user_id}/CLAUDE.md` |
| Daemon log | `~/.config/syke/daemon.log` |
| Daemon plist (macOS) | `~/Library/LaunchAgents/com.syke.daemon.plist` |
