# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![CI](https://github.com/saxenauts/syke/actions/workflows/ci.yml/badge.svg)](https://github.com/saxenauts/syke/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)

Syke is local-first memory for AI tools.

It watches the agent harnesses you already use, keeps durable memory in a local
SQLite store, and exposes the current map through `syke memex`, `syke ask`, and
agent capability registration.

The current release is built around one simple promise:

> Your AI tools should remember your work without sending your whole life to a
> new hosted memory service.

## What Syke Does

- Reads local agent activity from supported harnesses such as Claude Code, Codex,
  OpenCode, Cursor, GitHub Copilot, Antigravity, Hermes, and Gemini CLI.
- Synthesizes durable memory into `~/.syke/syke.db`.
- Projects the current working map into `~/.syke/MEMEX.md`.
- Lets you ask deeper questions with `syke ask`.
- Lets you save explicit notes with `syke record`.
- Runs a background daemon so memory can stay fresh without manual exporting.
- Installs Syke capability surfaces into detected agent environments.

## What Changed In 0.5.2

This is the release where Syke becomes a cleaner local runtime instead of a
pile of research-era plumbing.

- The old copy pipeline is gone. Syke no longer stages harness activity through
  a second event database.
- The memory store is one file: `~/.syke/syke.db`.
- Harness data stays where each tool wrote it. Syke installs adapter markdowns
  that teach the runtime how to read those artifacts directly.
- Pi is the canonical runtime for ask and synthesis.
- Provider state lives under `~/.syke/pi-agent/`, not in scattered shell state.
- Setup is inspect-then-apply, with JSON agent mode for automation.
- Source selection is persisted and reused by setup, sync, and daemon flows.
- The daemon reports process, registration, IPC, and warm runtime state instead
  of pretending everything is fine.
- Replay and benchmark tooling have moved out of this repo into a sibling lab.

## Quickstart

```bash
pipx install syke
syke setup
syke doctor
syke memex
syke ask "What changed this week?"
```

Alternative install:

```bash
uv tool install syke
syke setup
```

Agent/non-interactive setup:

```bash
syke setup --agent
```

`syke setup --agent` returns JSON with a `status` field:

- `needs_runtime` - install Node.js 18+ and rerun setup
- `needs_provider` - configure provider auth and rerun setup
- `complete` - setup finished
- `failed` - inspect the returned error

## Daily Commands

```bash
syke memex
syke ask "what should I remember about this project?"
syke record "The release blocker is daemon setup on macOS."
syke status
syke doctor
```

Background sync:

```bash
syke daemon start
syke daemon status
syke daemon logs
syke daemon stop
```

## Trust Model

Syke is intentionally local-first.

- Primary workspace: `~/.syke/`
- Mutable memory store: `~/.syke/syke.db`
- Current memex projection: `~/.syke/MEMEX.md`
- Identity/runtime prompt context: `~/.syke/PSYCHE.md`
- Adapter guides: `~/.syke/adapters/{source}.md`
- Pi provider/runtime state: `~/.syke/pi-agent/`

Ask and synthesis run through Pi inside Syke's workspace contract. On macOS,
Syke launches Pi with an OS sandbox that denies broad filesystem reads and only
allows catalog-scoped harness paths, Syke workspace writes, temp writes, and
network needed for provider calls.

## Setup And Source Selection

`syke setup` is inspect-then-apply. It reports detected providers, sources, and
planned writes before applying changes.

Source selection is a real persisted contract:

- Interactive setup lets you select detected sources.
- Automation can pass repeated `--source` values to `syke setup` or `syke sync`.
- Selected sources are saved at `~/.syke/source_selection.json`.
- Daemon and synthesis flows read the persisted selection.
- Invalid persisted selections fail closed instead of silently broadening scope.

## Providers

Syke uses Pi's provider catalog. Common flows:

```bash
syke auth set openai --api-key <KEY> --model gpt-5.4 --use
syke auth login openai-codex --use
syke auth set openrouter --api-key <KEY> --model openai/gpt-5.1-codex --use
syke auth status
```

Provider resolution order:

1. `--provider`
2. `SYKE_PROVIDER`
3. `~/.syke/pi-agent/settings.json`

Use `syke auth status` and `syke doctor` when behavior does not match what you
expected.

## Supported Harnesses

Active local harnesses currently include Claude Code, Codex, OpenCode, Cursor,
GitHub Copilot, Antigravity, Hermes, and Gemini CLI.

See [PLATFORMS.md](PLATFORMS.md) for exact artifact paths and status.

## Runtime And Replay Boundary

This repository is the product/runtime surface.

Replay, evaluation, benchmark orchestration, and research assets live in a
separate sibling repo:

```text
../syke-replay-lab
```

See [docs/RUNTIME_AND_REPLAY.md](docs/RUNTIME_AND_REPLAY.md) for the cross-repo
contract.

## Release Confidence

The release gate covers:

- full Python test suite
- ruff lint and format checks
- wheel build
- isolated wheel install smoke
- isolated `uv tool install` smoke
- daemon foreground smoke
- package surface checks so docs, scripts, research, and replay-lab internals do
  not ship inside the wheel

See [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) for the current
maintainer checklist.

## Docs

- [Setup Guide](docs/SETUP.md)
- [Providers](docs/PROVIDERS.md)
- [Config Reference](docs/CONFIG_REFERENCE.md)
- [Runtime and Replay](docs/RUNTIME_AND_REPLAY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Docs Index](docs/README.md)
