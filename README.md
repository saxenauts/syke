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
- Your agents can rely on MEMEX.md for cross harness continuity and collaboration
- Lets you ask deeper questions with `syke ask`.
- Lets you save explicit notes with `syke record`.
- Runs a background daemon so memory can stay fresh without manual exporting.
- Installs Syke capability surfaces into detected agent environments.

## Is this right for you?
If you use more than 1 coding harness actively daily. Yes. 
If you work on a single coding harness but concurrently (3-5 async sessions). Yes
If all your harness context fragmentation is on one device? Yes. No multi host support currently. 

Read more about the architecture choices and current direction below, read docs/ for more. 
TLDR:
- Any agents can work well as a good memory if you give them a file system, markdown and bash.
- All pre 2026 benchmarks for memory are saturated.
- WIP Environment and Benchmark for what memory means in 2026 with meta harnesses, RLM, hyperagents, GEPA etc. 
- Current Syke architecture is the simplest answer to the cross harness context and continuity drift problems
- Assumes no ontology and no deterministic scoping
- Updates itself
- Ambient Background Daemon on 15 minute cycle.
- Pi agent runtime. 



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

## Where This Is Heading

The hard question for personal memory isn't whether a model can recall
things. It's what *memory* even means for someone who spends most of their
life on computers. Markdown and a filesystem is good enough for a single
agent at smaller scales. Cross-harness work is chaotic in many many
different ways, and the interesting part is what happens when memory has
to fit one specific person, span every tool they use, and keep evolving
with them.

Syke's stance is **n = 1**. Every memory architecture has to be personalized
to its user and keep adapting as they change. There is no universal
answer — only the next iteration of yours.

While I work on the benchmarking side, the version that exists today is
good enough to use across your tools and play with. This is not the
intended use — the intended use is the right synthesis prompt paired with
measurables, and that comes later. In the meantime:

- **The synthesis prompt is yours.** Open `~/.syke/PSYCHE.md` and the
  synthesis skill at `syke/llm/backends/skills/pi_synthesis.md`. Edit them.
  Watch the memex change with you. The prompt is the experiment.
- **Make your own observations.** Run a few cycles, see what the memex
  looks like against your real work, then
  [open an issue](https://github.com/saxenauts/syke/issues) with what
  surprised you, what was useful, what felt off.
- **Bring the inspiration.** A lot of recent work points at how memory
  could behave inside agents. Pick the ideas that fit your life and try
  them. Syke is meant to be the substrate, not the answer.

**Fun tip:** edit the synthesis prompt to have the agent read its own
rollout traces and propose changes to its own memory. You've quietly
built a hyperagent meta-harness aimed at the memory problem itself. In
practice it tends toward self-absorbed behavior — balancing that against
measurable usefulness is exactly the kind of question good benchmarking
primitives would let us actually answer.

What I'm focused on next is the harder side: how do we even *measure*
memory. The goal is a practical modular environment formalisation that works on your data,
your workflow, your sense of what counts as remembering well and builds a benchmark for your use. If the
primitives hold up, we'll be able to say which architectures are better or
worse at which kinds of memory problems, instead of arguing about it in pre 2026 terms. 
Without that, iteration is guesswork, any architecture will give you SoTA

Issues, pull requests, and forks all welcome.

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

## macOS Permissions And Sandbox

Syke has two macOS safety layers:

- **Runtime sandbox:** ask and synthesis run Pi under `sandbox-exec` when
  available. The sandbox is deny-default for broad file reads, grants read-only
  access to selected harness roots, and grants write access to Syke's workspace,
  the active Pi state directory, and temp directories.
- **Launchd-safe daemon path:** background sync should not run directly from a
  source checkout under `~/Documents`, `~/Desktop`, or `~/Downloads`, because
  macOS TCC can block launchd from reading those paths. Syke uses a stable
  launcher under `~/.syke/bin/syke`; source checkouts may need
  `syke install-current` before background sync is enabled.

The sandbox is a filesystem boundary, not a network isolation system. Outbound
network is allowed so provider calls can work. Linux sandboxing with bubblewrap
is not claimed in this release.

## Setup And Source Selection

`syke setup` is inspect-then-apply. It reports detected providers, sources, and
planned writes before applying changes.

Source selection is a real persisted contract:

- Interactive setup lets you select detected sources.
- Automation can pass repeated `--source` values to `syke setup` or `syke sync`.
- Selected sources are saved at `~/.syke/source_selection.json`.
- Daemon and synthesis flows read the persisted selection.
- The runtime sandbox uses selected sources to narrow which harness roots Pi can
  read.
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

**Getting started**

- [Setup Guide](docs/SETUP.md)
- [Providers](docs/PROVIDERS.md)
- [Config Reference](docs/CONFIG_REFERENCE.md)

**Runtime**

- [Architecture](docs/ARCHITECTURE.md)
- [Runtime and Replay](docs/RUNTIME_AND_REPLAY.md)

**The story**

- [Memex Evolution](docs/MEMEX_EVOLUTION.md) — first chapter, how the memex routing pattern emerged (Feb 2026)
- [Memex Update 2](docs/MEMEX_UPDATE_2.md) — second chapter, the 0.5.2 cleanup (Apr 2026)

[Docs Index](docs/README.md) for the full listing with reading paths.
