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

**Kimi** — API key auth:
```bash
syke auth set kimi --api-key YOUR_KIMI_KEY
```

**Claude Code** — session auth, auto-detected if available:
```bash
claude login  # Requires Max/Team/Enterprise
```

**OpenAI-compatible providers** (via LiteLLM — included with syke):
```bash
syke auth set azure --api-key sk-xxx --endpoint https://my-deploy.openai.azure.com --model gpt-4o
syke auth set azure-ai --api-key sk-xxx --base-url https://my-project.services.ai.azure.com/models --model Phi-4
syke auth set openai --api-key sk-xxx --model gpt-4o
syke auth set ollama --model llama3.2                    # no API key needed
syke auth set vllm --base-url http://localhost:8000 --model mistral-7b
syke auth set llama-cpp --base-url http://localhost:8080 --model llama3.2
```

These providers use LiteLLM for automatic Anthropic-to-OpenAI translation. LiteLLM is included with Syke — no extra install. See `docs/CONFIG_REFERENCE.md` for provider-specific config options.

**Switch providers**:
```bash
syke auth use codex              # Set active provider
syke auth status                 # Show current provider + credentials
SYKE_PROVIDER=openrouter syke ask "question"  # One-time override
```

**Provider resolution precedence**: CLI `--provider` flag > `SYKE_PROVIDER` env var > `~/.syke/auth.json` active_provider > auto-detect.

Auth stored at `~/.syke/auth.json` as plaintext JSON with `0600` permissions. Codex tokens read from `~/.codex/auth.json` (managed by codex CLI).

### Agent-driven setup

By default, `syke setup` opens an interactive provider picker (arrow keys + Enter), then proceeds with ingest and daemon installation/start:

```bash
syke setup
```

For non-interactive runs, set provider explicitly using the root CLI flag or env var:

```bash
syke --provider codex setup --yes
SYKE_PROVIDER=codex syke setup --yes
```

Interactive picker example:
```
? Select provider for synthesis and ask
❯ claude-login   Claude Code session auth
  codex          ChatGPT Plus via Codex
  openrouter     OpenRouter (API key)
  zai            z.ai (API key)
  kimi           Kimi (API key)
```

`--yes` auto-consents to confirmations (daemon install/start) but does not change provider precedence rules.

Setup does not block on synthesis. It completes install/auth/ingest/daemon steps, and synthesis runs on the daemon's first tick.

---

## Configuration (optional)

Syke reads optional TOML config from `~/.syke/config.toml`. All settings have defaults.

```bash
syke config init      # Write default config.toml
syke config show      # Show effective config
syke config path      # Print config path
```

Use config when you want persistent overrides instead of per-command flags/env vars.

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
