# Syke Setup Guide

Canonical first-run path for the current Syke runtime.

Setup has one job: make Syke safe and boring to run on a real user's machine.
It should show what it found, ask before writing, persist the chosen sources and
provider, and leave the daemon in a state that `syke doctor` can explain.

## First-Run Path

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

`syke setup` is inspect-then-apply: it inspects provider/runtime/sources first,
shows planned actions, then applies on confirmation.

A healthy first run should end with:

- provider selected and daemon-safe
- detected sources selected or intentionally skipped
- `~/.syke/syke.db` initialized
- `~/.syke/MEMEX.md` available
- adapter markdowns installed under `~/.syke/adapters/`
- daemon install either confirmed or clearly skipped/explained

## Agent Mode (Non-Interactive)

```bash
syke setup --agent
```

`--agent` returns JSON with a `status` field:

- `needs_runtime` - install Node.js 18+ and rerun setup
- `needs_provider` - configure provider auth and rerun setup
- `complete` - setup finished
- `failed` - inspect the returned `error`

Agent mode is for automation. Humans should normally use plain `syke setup`
because it explains planned writes before applying them.

## Provider Setup

You can let interactive `syke setup` handle provider choice, or configure directly:

```bash
syke auth set openai --api-key <KEY> --model gpt-5.4 --use
syke auth status
```

Other common examples:

```bash
syke auth login openai-codex --use
syke auth set openrouter --api-key <KEY> --model openai/gpt-5.1-codex --use
syke auth set azure-openai-responses --api-key <KEY> --endpoint <URL> --model gpt-5.4-mini --use
syke auth set localproxy --base-url <URL> --model <MODEL> --use
```

Provider resolution order at runtime:

1. `--provider`
2. `SYKE_PROVIDER`
3. `~/.syke/pi-agent/settings.json` (`defaultProvider`)

Important: `--provider` and `SYKE_PROVIDER` are per-process overrides. The
daemon-safe provider is the persisted Pi state written by `syke setup`,
`syke auth set ... --use`, `syke auth login ... --use`, or `syke auth use`.

## Source Selection Contract

Source selection is persisted and reused across setup/sync/daemon flows.

- Interactive `syke setup` prompts for detected sources.
- Automation can pass repeated `--source` values to `syke setup`.
- `syke sync` accepts repeated `--source` values for the same persisted selection flow.
- Selections are stored at `~/.syke/source_selection.json`.
- If no selection exists yet, runtime behavior is unrestricted (`None` selection).
- If the persisted file is corrupt or names an unknown source, Syke fails closed
  to an empty selection instead of broadening access.

Notes:

- During setup, explicit `--source` values must be detected in that run or setup exits with a usage error.
- The `--source` option is intentionally hidden from `--help` output but is part of the supported setup/sync automation contract.

## What Setup Writes

Primary runtime artifacts are under `~/.syke/`:

- `syke.db`
- `MEMEX.md`
- `PSYCHE.md`
- `adapters/{source}.md`
- `pi-agent/auth.json`
- `pi-agent/settings.json`
- `pi-agent/models.json`
- `source_selection.json`

Daemon/system artifacts:

- `~/.config/syke/daemon.log`
- `~/Library/LaunchAgents/com.syke.daemon.plist` (macOS launchd installs)

Syke does not write replay or benchmark state into this repo. Replay-lab is a
separate sibling repository.

## Verify After Setup

```bash
syke status
syke auth status
syke daemon status
syke doctor
```

What to look for:

- `syke status` should show the selected provider, source selection, daemon
  process state, and daemon IPC state.
- `syke auth status` should show where provider/model/auth values came from.
- `syke daemon status` should distinguish process, launchd/cron registration,
  IPC reachability, and warm runtime binding.
- `syke doctor` should explain actionable failures instead of hiding them behind
  a generic unhealthy state.

## Daemon Behavior

On macOS, `syke daemon start` uses launchd. On other systems, Syke supports the
available cron/manual path.

The daemon is intentionally conservative:

- start should not report success unless the runtime is actually reachable
- stop should report incomplete shutdown if the process survives
- self-update should abort if the running daemon cannot be stopped safely
- daemon health treats runtime reachability as critical

## macOS Permissions And Sandbox

On macOS, Syke uses `sandbox-exec` around the Pi runtime for ask and synthesis.
The sandbox is intentionally scoped:

- selected harness roots are read-only
- Syke workspace and active Pi state are writable
- broad home-directory reads are denied
- sensitive directories such as `.ssh`, `.gnupg`, `.aws`, `.docker`, `.kube`,
  and gcloud config are explicitly denied
- outbound network is allowed for provider calls

This is separate from launchd/TCC. If Syke is launched from a source checkout
under `~/Documents`, `~/Desktop`, or `~/Downloads`, background launchd jobs can
be blocked by macOS. In that case use:

```bash
syke install-current
syke setup
```

Linux does not yet have an equivalent bubblewrap sandbox guarantee in this
release.

## Troubleshooting

- `needs_runtime` from `syke setup --agent`: install Node.js 18+.
- Provider/auth failures: run `syke auth status` then `syke doctor`.
- Empty/old memex: run `syke sync`, then `syke memex`.
- Background sync unavailable on macOS source checkouts under protected folders: use `syke install-current` and rerun setup.
- `syke ask --json` exits non-zero: read the structured `error` field. Do not
  treat a backend/runtime error as an answer.
- Daemon running but ask path feels stale: run `syke daemon status` and compare
  the warm runtime provider/model with `syke auth status`.

## Related Docs

- [README](../README.md)
- [Providers](PROVIDERS.md)
- [Config Reference](CONFIG_REFERENCE.md)
- [Runtime And Replay](RUNTIME_AND_REPLAY.md)
- [Scripts Surface](../scripts/README.md)
