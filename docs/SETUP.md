# Syke Setup Guide

Canonical first-run path for the current local-first Syke runtime.

This guide is agent-first: an agent dropped into the repo should be able to follow it directly. A human can follow the same steps manually.

---

## First-Run Path

```bash
pipx install syke

# choose one provider
codex login
syke auth use codex
# or:
syke auth set openai --api-key YOUR_KEY --model gpt-5-mini --use

syke setup
syke doctor
syke ask "What changed this week?"
syke context
syke daemon status
```

Use `syke auth status` at any point to confirm the resolved provider, auth source, model, and endpoint.

---

## What Setup Does

Current setup is centered on the core loop:

1. validate or choose an LLM provider
2. detect local sources
3. bootstrap missing Observe adapters into `~/.syke/data/{user}/adapters`
4. ingest observed data into the immutable timeline
5. install/start the background loop on macOS
6. let synthesis update the memex on the loop

The main product artifacts after setup are:

- `~/.syke/data/{user}/events.db`
- `~/.syke/data/{user}/syke.db`
- `~/.syke/data/{user}/adapters/`
- `~/.syke/workspace/events.db`
- `~/.syke/workspace/syke.db`
- `~/.syke/workspace/MEMEX.md`
- `~/.syke/auth.json`

Harness-specific projections such as `CLAUDE.md` may also be installed, but they are downstream distribution targets, not the canonical runtime artifact model.

First-run setup now treats Observe adapter bootstrap as part of onboarding. If a supported local harness is detected and its adapter is missing, setup generates or repairs that adapter before the first ingest pass instead of assuming `~/.syke/data/{user}/adapters` already exists.

---

## Prerequisites

- Python 3.12+
- `pipx` or `uv`
- one working provider
- local data for at least one supported source

Current release reality:

- macOS-first daemon workflow
- memex-first system
- active local sources are Claude Code, Codex, ChatGPT export, and current harness/distribution paths
- GitHub is not part of the main setup path right now

---

## Install

```bash
pipx install syke
syke auth use codex
syke setup
```

Alternative:

```bash
uv tool install syke
syke auth use codex
syke setup
```

Development install:

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
uv sync --extra dev --locked
uv run syke auth use codex
uv run syke setup
```

`uv` will create or reuse the repo-local `.venv` automatically. Use `uv run ...` for commands instead of manually managing activation.

Important macOS note:

- LaunchAgent installs now run through the stable launcher at `~/.syke/bin/syke`
- if your source checkout lives under `~/Documents`, `~/Desktop`, or `~/Downloads`, launchd may be blocked by TCC from executing that source-dev runtime and the daemon install now only targets a safe non-editable installed `syke` whose install origin matches this checkout; otherwise install fails with guidance instead of silently registering the wrong binary
- editable installs that import directly from a protected checkout are not launchd-safe on macOS
- for a background daemon on macOS, prefer a safe installed path such as `pipx install syke`, `uv tool install syke`, `pipx install .`, `uv tool install --force --reinstall --refresh --no-cache .`, or run `syke install-current`
- consult the install-surface matrix in `docs/PACKAGING_AND_INSTALL.md` before picking a distribution path so that every surface answers the same runtime contract questions
- if you stay in repo-dev mode under a protected directory, use `uv run syke daemon run ...` in the foreground instead of installing launchd

---

## Provider Setup

Use one of the providers Syke supports today. If you run `syke auth set ...`, add `--use` when you want that provider to become active immediately.

### Codex

```bash
codex login
syke auth use codex
```

### API-key providers

```bash
syke auth set openrouter --api-key YOUR_KEY --use
syke auth set zai --api-key YOUR_KEY --use
syke auth set kimi --api-key YOUR_KEY --use
```

### Pi runtime providers

```bash
syke auth set azure --api-key KEY --endpoint URL --model MODEL --use
syke auth set openai --api-key KEY --model MODEL --use
syke auth set ollama --model llama3.2 --use
syke auth set vllm --base-url URL --model MODEL --use
syke auth set llama-cpp --base-url URL --model MODEL --use
```

Check state:

```bash
syke auth status
syke auth status --json
syke doctor
```

Provider resolution order:

1. `--provider`
2. `SYKE_PROVIDER`
3. `~/.syke/auth.json` active provider

---

## Main Setup Flow

Interactive:

```bash
syke setup
```

Non-interactive:

```bash
syke --provider codex setup --yes
```

What to expect:

- provider validation or interactive selection
- explicit runtime summary: provider, auth source, model, endpoint
- source detection
- initial ingest
- background-loop install on macOS
- synthesis later on the loop, not as the blocking centerpiece of setup

---

## Source Notes

### Claude Code

Automatic local detection from `~/.claude`.

### Codex

Automatic local detection from `~/.codex`.

### ChatGPT export

Manual import from an export ZIP:

```bash
syke ingest chatgpt --file ~/Downloads/your-export.zip
```

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
| Auth store | `~/.syke/auth.json` |
| Stable Syke launcher | `~/.syke/bin/syke` |
| Daemon log | `~/.config/syke/daemon.log` |
| macOS launch agent | `~/Library/LaunchAgents/com.syke.daemon.plist` |

Note: `syke.db` is the authoritative mutable store, and the memex is routed into the Pi workspace as `MEMEX.md`. Workspace `events.db` is a snapshot of the canonical user ledger. Files such as `CLAUDE.md` are harness-specific distribution targets.

---

## Troubleshooting

| Problem | What to check |
|---|---|
| `syke` not found | reinstall with `pipx` or `uv tool` |
| provider errors | `syke auth status`, `syke doctor` |
| empty memex | setup/ingest may have succeeded before enough useful synthesis happened |
| `ask` fails | provider/auth/runtime issue; use `syke doctor` and `syke context` |
| `ask` fails only inside another agent sandbox | use `syke context` or the distributed memex there, and run `syke ask` from a trusted host shell |
| no background loop | check `syke daemon status` on macOS |

---

## For Agents

If you are an agent setting this repo up for a user:

1. verify a provider first
2. prefer the main `syke setup` flow over ad hoc commands
3. confirm with `syke doctor`
4. inspect `syke context`
5. only then debug source-specific issues
