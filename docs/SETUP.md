# Syke Setup Guide

Canonical first-run path for the current local-first Syke runtime.

This guide is agent-first: an agent dropped into the repo should be able to follow it directly. A human can follow the same steps manually.

---

## First-Run Path

```bash
pipx install syke

syke setup          # inspect available providers/sources, then confirm ingest/daemon plan
syke doctor         # check runtime, trust, and health
syke ask "What changed this week?"
syke context
syke daemon status
```

`syke setup` summarizes the providers it found, the sources it can reach, and what targets would be written before you confirm ingestion or daemon installation. If an active provider is already configured and healthy, setup keeps it instead of reprompting. When setup ingests new data or detects a cold start with no memex yet, it also runs an initial synthesis immediately so `syke context` is useful right away.

If you prefer to configure a provider first, use `syke auth set ... --use` for API-key providers or `syke auth login ... --use` for Pi-native OAuth providers.

---

## What Setup Does

Current setup is centered on the inspect-then-apply loop:

1. detect available providers, sources, and trust targets
2. report what would happen (files written, adapters created, daemon changes) so you can review the plan
3. let you review and confirm the setup actions before anything is written
4. run the confirmed actions and persist the canonical artifacts
5. run an initial synthesis when setup created or materially changed state, then let the background loop keep it fresh

The main product artifacts after setup are:

- `~/.syke/data/{user}/events.db`
- `~/.syke/data/{user}/syke.db`
- `~/.syke/data/{user}/adapters/`
- `~/.syke/workspace/events.db`
- `~/.syke/workspace/syke.db`
- `~/.syke/workspace/MEMEX.md`
- `~/.syke/pi-agent/auth.json`
- `~/.syke/pi-agent/settings.json`
- `~/.syke/pi-agent/models.json`

The downstream distribution refresh now touches only the exported memex and the registered Syke capability package on supported harness capability surfaces. Those are projections, not the canonical runtime artifact model.

First-run setup now treats Observe adapter bootstrap as seed-first onboarding. If a supported local harness is detected, setup validates the shipped seed adapter first, deploys it if it passes, and only falls back to the factory when the shipped seed is missing or no longer matches the local artifact shape.

---

## Prerequisites

- Python 3.12+
- `pipx` or `uv`
- access to one provider or account path
- local data for at least one supported source

Current release reality:

- launchd on macOS, cron on other platforms when `crontab` is available
- memex-first system
- active local sources are Claude Code, Codex, OpenCode, Cursor, GitHub Copilot, Antigravity, Hermes, and Gemini CLI
- GitHub is not part of the main setup path right now

---

## Install

```bash
pipx install syke
syke setup
```

Alternative:

```bash
uv tool install syke
syke setup
```

Development install:

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
uv sync --extra dev --locked
uv run syke setup
```

`uv` will create or reuse the repo-local `.venv` automatically. Use `uv run ...` for commands instead of manually managing activation.

Important macOS note:

- LaunchAgent installs now run through the stable launcher at `~/.syke/bin/syke`
- if your source checkout lives under `~/Documents`, `~/Desktop`, or `~/Downloads`, launchd may be blocked by TCC from executing that source-dev runtime and the daemon install now only targets a safe non-editable installed `syke` whose install origin matches this checkout; otherwise install fails with guidance instead of silently registering the wrong binary
- editable installs that import directly from a protected checkout are not launchd-safe on macOS
- for a background daemon on macOS, prefer a safe installed path such as `pipx install syke`, `uv tool install syke`, `pipx install .`, `uv tool install --force --reinstall --refresh --no-cache .`, or run `syke install-current`
- if you stay in repo-dev mode under a protected directory, use `uv run syke daemon run ...` in the foreground instead of installing launchd

---

## Provider Setup

Syke works with multiple providers. Configure whichever one you already trust, or let `syke setup` walk you through the choice. Use `syke auth set <provider> ... --use` to make a provider active after you supply the needed credentials or endpoint information.

| Provider Class | Example command | Notes |
| --- | --- | --- |
| API-key Pi providers | `syke auth set openai --api-key KEY --model gpt-5.4 --use` | Use Pi provider IDs such as `openai`, `openrouter`, `zai`, `kimi-coding`, or `azure-openai-responses`. |
| Pi-native OAuth providers | `syke auth login openai-codex --use` | Uses Pi's native login flow and stores the result in `~/.syke/pi-agent/auth.json`. |
| Custom OpenAI-compatible endpoint | `syke auth set localproxy --base-url URL --model MODEL --use` | Use this for local or self-hosted OpenAI-compatible runtimes that are not in Pi's built-in catalog. |
Provider resolution order:

1. `--provider`
2. `SYKE_PROVIDER`
3. `~/.syke/pi-agent/settings.json` `defaultProvider`

Important:

- `--provider` and `SYKE_PROVIDER` are per-process overrides.
- Setup and daemon-safe background use rely on persisted Pi-owned state in `~/.syke/pi-agent/`, not repo-local `.env` files.

---

## Main Setup Flow

Interactive:

```bash
syke setup
```

Non-interactive:

```bash
syke setup --json
```

What to expect:

- provider validation or interactive selection
- explicit runtime summary: provider, auth source, model, endpoint
- live Pi probe before setup continues past provider activation
- inspect-only JSON mode for another agent to review before acting
- source detection
- initial ingest
- background-loop install where supported
- first-run synthesis when setup materially changed state
- downstream distribution refresh for the exported memex and the registered Syke capability package on supported harness capability surfaces

---

## Source Notes

### Claude Code

Automatic local detection from `~/.claude`.

### Codex

Automatic local detection from `~/.codex`, with SQLite state under
`~/.codex/sqlite` and append-only JSONL history/index files under `~/.codex`.

### Cursor

Automatic local detection from official Cursor user-data roots such as:

- `~/Library/Application Support/Cursor/User/workspaceStorage`
- `~/Library/Application Support/Cursor/User/globalStorage`
- `~/.config/Cursor/User/workspaceStorage`
- `~/.config/Cursor/User/globalStorage`

Setup targets workspace chat/session artifacts and Cursor state DBs from those roots, not the legacy `~/.cursor/**` cache/extension surface.

### GitHub Copilot

Automatic local detection from:

- `~/.copilot/session-state` for Copilot CLI sessions
- VS Code user-data `workspaceStorage/*/chatSessions` and `globalStorage/emptyWindowChatSessions`

### Antigravity

Automatic local detection from `~/.gemini/antigravity`.

Current support treats Antigravity as a workflow-artifact timeline surface: task lists, implementation plans, walkthroughs, and browser recording metadata.

### Hermes

Automatic local detection from `~/.hermes`, with `state.db` plus session JSON artifacts under `~/.hermes/sessions`.

### Gemini CLI

Automatic local detection from `~/.gemini/tmp`, focused on:

- `~/.gemini/tmp/<project_hash>/chats/**/*.json`
- `~/.gemini/tmp/<project_hash>/checkpoints/**/*.json`

### GitHub

Not part of the main setup path right now.

---

## After Setup

```bash
syke doctor
syke ask "what was I working on recently?"
syke context
syke status
syke daemon status
```

- `syke ask` can go deeper than the current memex
- `syke context` shows the current routed `MEMEX.md` projection
- `syke status` shows ingestion + memex state plus the resolved runtime provider, auth source, model, and endpoint
- `syke daemon status` is the background-loop status view
- some agent sandboxes can read the distributed memex but cannot invoke `syke ask` against the live store directly yet

---

## File Locations

| What | Where |
|---|---|
| User data | `~/.syke/data/{user}/` |
| User evidence ledger | `~/.syke/data/{user}/events.db` |
| Main Syke store | `~/.syke/data/{user}/syke.db` |
| Runtime workspace events snapshot | `~/.syke/workspace/events.db` |
| Runtime workspace memory store | `~/.syke/workspace/syke.db` |
| Runtime workspace memex projection | `~/.syke/workspace/MEMEX.md` |
| Pi auth store | `~/.syke/pi-agent/auth.json` |
| Pi active provider/model | `~/.syke/pi-agent/settings.json` |
| Pi provider overrides | `~/.syke/pi-agent/models.json` |
| Stable Syke launcher | `~/.syke/bin/syke` |
| Daemon log | `~/.config/syke/daemon.log` |
| macOS launch agent | `~/Library/LaunchAgents/com.syke.daemon.plist` |

Note: `syke.db` is the authoritative mutable store, and the memex is routed into the Pi workspace as `MEMEX.md`. Workspace `events.db` is a snapshot of the canonical user ledger. External files such as `~/.syke/data/{user}/MEMEX.md` and registered Syke capability files are downstream projections.

---

## Troubleshooting

| Problem | What to check |
|---|---|
| `syke` not found | reinstall with `pipx` or `uv tool` |
| provider errors | `syke auth status`, `syke doctor` |
| empty memex | setup/ingest may have succeeded before enough useful synthesis happened |
| `ask` fails | provider/auth/runtime issue; use `syke doctor` and `syke context` |
| `ask` fails only inside another agent sandbox | use `syke context` or the distributed memex there, and run `syke ask` from a trusted host shell |
| no background loop | check `syke daemon status` and `syke daemon logs`; immediately after install the daemon may still be warming and `warm ask` may not be ready yet |

### Dev Reset

For a real clean local repro on macOS, do not only `rm -rf ~/.syke`.
Use the repo reset script instead:

```bash
bash scripts/dev-reset.sh --yes
```

That script:

- stops and unloads the launchd daemon
- removes `~/Library/LaunchAgents/com.syke.daemon.plist`
- removes `~/.config/syke`
- removes `~/.syke`
- best-effort uninstalls `syke` from `uv tool` / `pipx`

If you want to keep the installed binary and only reset state + daemon files:

```bash
bash scripts/dev-reset.sh --yes --keep-tool
```

---

## For Agents

If you are an agent setting this repo up for a user:

1. verify a provider first
2. prefer the main `syke setup` flow over ad hoc commands
3. confirm with `syke doctor`
4. inspect `syke context`
5. only then debug source-specific issues
