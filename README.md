# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![CI](https://github.com/saxenauts/syke/actions/workflows/ci.yml/badge.svg)](https://github.com/saxenauts/syke/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)

Syke is local-first memory for AI tools. It ingests local harness activity, synthesizes durable memory, and exposes that memory through `syke context`, `syke ask`, and capability registration.

## Quickstart

```bash
pipx install syke
syke setup
syke doctor
syke context
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

`syke setup --agent` returns JSON with a `status` field (`needs_runtime`, `needs_provider`, `complete`, or `failed`).

## Core CLI Surface

```bash
syke setup
syke ask "question"
syke context
syke record "note"
syke status
syke sync
syke auth
syke doctor
```

Background sync commands:

```bash
syke daemon start
syke daemon stop
syke daemon status
syke daemon logs
```

## Setup and Source Selection Contract

`syke setup` is inspect-then-apply: it reports providers, detected sources, and planned writes before applying changes.

Source selection is a persisted contract:

- Interactive setup lets you select detected sources.
- Automation can pass `--source` repeatedly to `syke setup` (and `syke sync`) to persist selected sources.
- Selected sources are saved at `~/.syke/source_selection.json`.
- Daemon and synthesis flows read that persisted selection.

## Workspace Artifacts

Everything lives under `~/.syke/`:

- `syke.db` — canonical mutable store
- `MEMEX.md` — projected memex for downstream context
- `PSYCHE.md` — identity and runtime prompt context
- `adapters/{source}.md` — harness adapter markdowns
- `pi-agent/{auth.json,settings.json,models.json}` — provider/runtime state

## Supported Harnesses

Active local harnesses currently include Claude Code, Codex, OpenCode, Cursor, GitHub Copilot, Antigravity, Hermes, and Gemini CLI.

See [PLATFORMS.md](PLATFORMS.md) for exact artifact paths and status.

## Runtime and Replay Boundary

This repo is the product/runtime surface.

Replay/eval/research tooling is intentionally separate in a sibling repo:

- `../syke-replay-lab`

See [docs/RUNTIME_AND_REPLAY.md](docs/RUNTIME_AND_REPLAY.md) for the cross-repo contract.

## Docs

- [Setup Guide](docs/SETUP.md)
- [Providers](docs/PROVIDERS.md)
- [Config Reference](docs/CONFIG_REFERENCE.md)
- [Runtime and Replay](docs/RUNTIME_AND_REPLAY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Docs Index](docs/README.md)
