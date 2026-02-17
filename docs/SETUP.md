# Syke Setup Guide

Step-by-step setup for running Syke locally or on a cloud instance.

---

## Prerequisites

- Python 3.12+ (tested on 3.14)
- An Anthropic API key with Opus 4.6 access
- Git

---

## Step 1: Clone and Create Environment

```bash
git clone https://github.com/saxenauts/syke.git
cd syke
python3 -m venv .venv
source .venv/bin/activate
```

## Step 2: Install Dependencies

```bash
pip install -e .
```

If you get errors, install individually:
```bash
pip install anthropic click pydantic pydantic-settings rich python-dotenv uuid7 \
    beautifulsoup4 lxml google-auth-oauthlib google-api-python-client mcp
```

## Step 3: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Optional (add as you need them):
```
GITHUB_TOKEN=ghp_your-token-here
GMAIL_CREDENTIALS_PATH=~/.config/syke/gmail_credentials.json
GMAIL_TOKEN_PATH=~/.config/syke/gmail_token.json
SYKE_USER=your-name-here
```

## Step 4: Verify Installation

```bash
# Should show all commands
python -m syke --help

# Should show check results
python -m syke health

# Should show empty status
python -m syke status
```

---

## Quick Try: GitHub Ingestion (Easiest, No OAuth)

This is the fastest way to see Syke work end-to-end. Only needs a GitHub username (no token required for public data).

```bash
# 1. Ingest your GitHub data
python -m syke ingest github --username YOUR_GITHUB_USERNAME

# 2. Check what came in
python -m syke status
python -m syke timeline --limit 10

# 3. Run Opus 4.6 perception (requires ANTHROPIC_API_KEY)
python -m syke perceive

# 4. See the profile
python -m syke profile --format markdown
python -m syke profile --format claude-md

# 5. Check what it cost
python -m syke metrics
```

That's it. You just went from raw GitHub data to a perceived identity profile.

---

## Full Pipeline: Multiple Sources

### Gmail Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project, enable Gmail API
3. Create OAuth 2.0 credentials (Desktop app)
4. Download `credentials.json` to `~/.config/syke/gmail_credentials.json`
5. Run:

```bash
python -m syke ingest gmail
# First run opens browser for OAuth consent
# Token is saved for future runs
```

### ChatGPT Export

1. Go to ChatGPT → Settings → Data Controls → Export Data
2. Wait for email with download link (can be hours)
3. Download the ZIP file
4. Run:

```bash
python -m syke ingest chatgpt --file ~/Downloads/your-export.zip
```

### GitHub (with token for private repos)

1. Go to GitHub → Settings → Developer settings → Personal access tokens
2. Create a token with `repo` and `read:user` scopes
3. Add to `.env`: `GITHUB_TOKEN=ghp_...`
4. Run:

```bash
python -m syke ingest github --username YOUR_USERNAME
```

### After Ingesting Multiple Sources

```bash
# Re-run perception with all data
python -m syke perceive

# The profile now cross-references across platforms
python -m syke profile --format claude-md
```

---

## MCP Server Setup (Claude Code Integration)

Once you have a profile, you can make it available to Claude Code via MCP.

### Option A: Add to project settings

Add to your project's `.claude/settings.json`:

```json
{
  "mcpServers": {
    "syke": {
      "command": "/path/to/syke/.venv/bin/python",
      "args": ["-m", "syke", "serve", "--transport", "stdio"],
      "cwd": "/path/to/syke"
    }
  }
}
```

### Option B: Inject as a CLAUDE.md file

```bash
python -m syke inject --target /path/to/any/project/.claude --format claude-md
```

This writes a `CLAUDE.md` into the target project that Claude Code will read.

---

## Monitoring and Health

```bash
# Health check — shows what's configured and working
python -m syke health

# Metrics — shows cost, tokens, timing for all operations
python -m syke metrics

# Status — shows ingested data counts by source
python -m syke status

# Logs — structured log file
cat data/<user_id>/syke.log
```

---

## Cloud Instance Setup

For running on a fresh VM (e.g., Claude Cloud Code):

```bash
# 1. Clone
git clone https://github.com/saxenauts/syke.git && cd syke

# 2. Setup Python
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Set API key (use environment variable or .env)
export ANTHROPIC_API_KEY=sk-ant-...

# 4. Quick test
python -m syke health
python -m syke ingest github --username YOUR_USERNAME
python -m syke perceive
python -m syke profile --format markdown
```

No GUI needed — everything runs in the terminal.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` | Make sure venv is activated: `source .venv/bin/activate` |
| Health check shows `FAIL anthropic_key` | Set `ANTHROPIC_API_KEY` in `.env` |
| Gmail says "credentials not found" | Download OAuth credentials from Google Cloud Console |
| GitHub returns 403 | Rate limited — add `GITHUB_TOKEN` to `.env` |
| Perception returns empty | Need at least some events ingested first — check `syke status` |
| `pip install -e .` fails | Try `pip install -r requirements.txt` or install deps manually |

---

## File Locations

| What | Where |
|------|-------|
| Configuration | `.env` in project root |
| User data | `data/{user_id}/` |
| SQLite database | `data/{user_id}/syke.db` |
| Latest profile | `data/{user_id}/profile.json` |
| Metrics log | `data/{user_id}/metrics.jsonl` |
| Application log | `data/{user_id}/syke.log` |
| Strategy files | `strategies/*.md` |
