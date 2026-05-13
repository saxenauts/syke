# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![CI](https://github.com/saxenauts/syke/actions/workflows/ci.yml/badge.svg)](https://github.com/saxenauts/syke/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)

Syke is local-first memory for AI agents.

It watches the coding harnesses you already use, builds a durable local MEMEX,
and gives agents a shared memory surface through CLI commands and installed
capability files. The goal is simple:

> Your AI tools should remember your work without sending your whole life to a
> hosted memory service.

Syke is most useful when your work is fragmented across multiple agents,
sessions, projects, or harnesses. The daemon keeps a background cadence, the
local timeline shows what changed, and `syke ask` gives agents a way to recall
and reason over the stitched history.

## What Syke Does

- Reads local agent activity from supported harnesses such as Claude Code, Codex,
  OpenCode, Cursor, GitHub Copilot, Antigravity, Hermes, and Gemini CLI.
- Synthesizes durable memory into `~/.syke/syke.db`.
- Projects the current working map into `~/.syke/MEMEX.md`.
- Gives agents a stable memory surface for cross-harness continuity.
- Lets you ask deeper questions with `syke ask`.
- Lets you save explicit notes with `syke record`.
- Runs a background daemon so memory can stay fresh without manual exporting.
- Serves a local timeline UI so you can inspect cycles, asks, diffs, and traces.
- Installs Syke capability files into detected agent environments.

## Is this right for you?

Syke is a fit if:

- You use more than one coding harness regularly.
- You run multiple agent sessions concurrently and lose continuity between them.
- Your important agent context lives on this machine.
- You want a local memory substrate agents can inspect and update.

Current limit: Syke is local-machine first. Multi-host memory sync is not part
of this release.

## Quickstart (Manual)

```bash
pipx install syke
syke setup
syke web --open
syke memex
syke ask "What changed this week?"
```

Alternative install:

```bash
uv tool install syke
syke setup
```

`syke setup` is the human path. It inspects your machine, shows the provider,
runtime, harnesses, and planned writes, then asks before applying the setup.

After setup, keep working. First synthesis can take a little while depending on
how much local harness history Syke finds. The local timeline explains the
current state while the first MEMEX is being built.

## Quickstart (Agent / Non-Interactive)

```bash
syke setup --agent
```

`syke setup --agent` returns structured JSON:

- `needs_runtime` - install Node.js 18+ and rerun setup
- `needs_provider` - configure provider auth and rerun setup
- `complete` - setup finished
- `failed` - inspect `error`

Recommended contract:

```bash
# 1) Probe setup state (machine-readable)
syke setup --agent

# 2) If status=needs_provider, configure auth then rerun
syke auth set <provider> --api-key <KEY> --use
syke setup --agent

# 3) For CI/smoke or ephemeral runs, skip daemon and run one explicit sync
syke setup --agent --skip-daemon
syke sync
```

Agent rule of thumb: run setup once, parse the JSON, take the next step it gives
you, and do not loop on `syke setup` after `status=complete`. If the next step
is `syke sync`, wait for it once; the result reports duration, trace ID, MEMEX
state, and follow-up commands. Use `syke status --json` and the timeline health
API for monitoring. Use `syke doctor --json` as a gate: it exits non-zero when
any check fails.

For teams running from a repository checkout and wanting a one-command bootstrap script:

```bash
bash install_syke.sh
```

`install_syke.sh` is agent-friendly and can auto-configure provider auth from env:

```bash
SYKE_PROVIDER=openai \
SYKE_API_KEY=<KEY> \
SYKE_MODEL=gpt-5.4 \
bash install_syke.sh
```

By default the script lets Syke enable the daemon after provider auth is ready,
which is the real user path. Set `SYKE_SKIP_DAEMON=1` only for CI, tests, or
throwaway machines where launchd/cron side effects are not wanted.

## First-Run Timeline State

What happens after setup:

1. Syke detects available harness data roots and selected sources.
2. A synthesis cycle reads and stitches timeline evidence across those harnesses.
3. Canonical memory is committed to `~/.syke/syke.db`.
4. Current projection is exported to `~/.syke/MEMEX.md`.
5. Timeline HTML/API starts reflecting cycles/asks as they appear.

What the user should do:

- Open the local timeline with `syke web --open`.
- Keep working while the first synthesis runs.
- Check `syke status` or `syke doctor` if the page says setup is blocked.
- Run `syke ask "what am I working on?"` once MEMEX starts landing.

What the timeline may show:

- **MEMEX is bootstrapping** — the daemon is running first synthesis.
- **MEMEX bootstrap is waiting** — setup is complete, but sync/daemon is not running.
- **No harness history detected yet** — nothing local was found yet; future work,
  `syke record`, and harness activity can still create memory.

Timing depends on detected source volume. Agent setup reports:

- `estimated_minutes`
- `total_files`
- `estimate_method` (`max(2, total_files // 1500 + 3)`)

Clean-room runs with near-empty local history can finish in under a minute.
Heavier histories can take several minutes on first pass.

`syke ask` can run before the first MEMEX exists, but it will be sparse. After
the setup-recommended `syke sync` completes, move on with normal work and use
`syke ask` when needed.

## Persistence

On macOS, `syke daemon start` installs a launchd agent with `RunAtLoad` and
`KeepAlive`. If the daemon exits unexpectedly, launchd restarts it. The same
daemon process keeps memory fresh and serves the local timeline UI.

On non-macOS systems, Syke currently uses cron/manual paths for periodic sync.
That preserves sync cadence, but it is not the same as a resident self-healing
timeline server.

If the daemon is running before provider/model setup is complete, it backs off
instead of spamming failed cycles. Finish auth with `syke auth ... --use`, then
rerun setup or sync.

## Fresh Setup Test (No Data Loss)

Use an isolated `HOME` so your real `~/.syke` stays untouched:

```bash
FRESH_HOME="$HOME/.syke-fresh-home"
rm -rf "$FRESH_HOME"
mkdir -p "$FRESH_HOME"

HOME="$FRESH_HOME" uv tool install syke
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh setup --agent
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh status --json
```

Full agent smoke (with auth available in that fresh profile):

```bash
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh setup --agent --skip-daemon
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh sync
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh ask --json "what am I working on"
```

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

## Local Timeline UI

The daemon serves a read-only timeline of your synthesis cycles, asks,
and the memex itself — bound to `127.0.0.1:8765` only.
This is part of normal daemon runtime behavior (same process): memory stays
fresh on the sync cadence, and the local HTML/API timeline surface stays up
while the daemon is running.

```bash
syke daemon start    # if it isn't already running
syke web             # prints the URL
syke web --open      # opens it in your default browser
```

What you'll see:

- A first-run timeline state when the timeline is still empty: setup state,
  sync hints, and next CLI commands inside the normal timeline view.
- A 7-day scrubber. Each cycle and each ask is a tick. Click one or use
  `←` / `→` to step through. `Shift+←/→` jumps day boundaries.
- **Memex** tab — the projection at that moment, content or diff.
- **Memory** tab — every active memory as a cell grid. Click a cell;
  every memory linked to it lights up. The grid persists as you scrub.
- **Trace** tab — the agent's full transcript: thinking, tool use, and
  results, per turn.
- Live tail of `~/.config/syke/daemon.log` at the bottom (`L` toggles).
- Day / night theme toggle in the header.

Configuration:

- `SYKE_WEB_PORT` — change the port (default `8765`).
- `SYKE_WEB_ENABLED=0` — disable the server (daemon keeps running).

## Where This Is Heading

The hard question for personal memory isn't whether a model can recall
things. It's what *memory* even means for someone who spends most of their
life on computers. Markdown and a filesystem is good enough for a single
agent at smaller scales. Cross-harness work is chaotic in many many
different ways, and the interesting part is what happens when memory has
to fit one specific person, span every tool they use, and keep evolving
with them.

Syke's stance is **n = 1**. Every memory architecture has to be personalized 
to its user and keep adapting as they change.

While I work on the benchmarking side, the version that exists today is
good enough to use across your tools and play with. In the meantime:

- **The synthesis prompt is yours.** Open `~/.syke/PSYCHE.md` and the
  synthesis skill at `syke/llm/backends/skills/pi_synthesis.md`. Edit them.
  Watch the memex change with you.
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
practice it tends toward self-absorbed behavior and wastes token on 
self analysis. Balancing this requires designing right evaluation.

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

Run `bash scripts/release-preflight.sh` before tagging.

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
