# Syke Setup Guide

Canonical first-run path for humans, agents, and release smoke tests.

Setup has one job: make Syke safe and boring to run on a real user's machine.
It should show what it found, ask before writing, persist the chosen sources and
provider, and leave the daemon in a state that `syke status`, `syke doctor`, and
the local timeline can explain.

## Human First Run

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

`syke setup` is inspect-then-apply: it inspects provider/runtime/sources first,
shows planned actions, then applies on confirmation.

The user experience should be:

1. See what Syke found: provider, runtime, harnesses, and planned writes.
2. Choose or confirm sources.
3. Confirm provider/auth.
4. Let setup start background sync/bootstrap unless intentionally skipped.
5. Open the timeline and keep working while first synthesis runs.

A healthy first run should end with:

- provider selected and daemon-safe
- detected sources selected or intentionally skipped
- `~/.syke/syke.db` initialized
- `~/.syke/MEMEX.md` available
- adapter markdowns installed under `~/.syke/adapters/`
- daemon install either confirmed or clearly skipped/explained
- local timeline available through `syke web`

## Agent Mode (Non-Interactive)

```bash
syke setup --agent
```

`--agent` returns JSON with a `status` field:

- `needs_runtime` - install Node.js 20+ (22 LTS recommended) and rerun setup
- `needs_provider` - configure provider auth and rerun setup
- `complete` - setup finished
- `failed` - inspect the returned `error`

Agent mode is for automation. Humans should normally use plain `syke setup`
because it explains planned writes before applying them.

Agent payload fields that matter for orchestration:

- `status`, `exit_code`, `instructions`, `next_steps`
- `estimated_minutes`, `total_files`, `estimate_method`
- `daemon` (`started` vs `skipped`)
- `daemon_persistence`
- `monitor`
- `onboarding`

Recommended automation flow:

1. Run `syke setup --agent` and parse `status`.
2. If `needs_provider`, run `syke auth set <provider> --api-key <KEY> --use`
   (or `syke auth login <provider> --use`) and rerun setup.
3. For CI/smoke or ephemeral environments, use `syke setup --agent --skip-daemon`,
   then run one explicit `syke sync`.
4. Only enable daemon setup in environments where launchd/systemd side effects are intended.

After manual `syke sync`, the JSON payload includes `duration_ms`, `trace_id`,
`tool_calls`, `num_turns`, `model`, `cost_usd`, `memex_updated`, and
`next_steps`. Agents should treat that as the handoff point: if `status` is
`completed`, stop setup work, move on with normal user work, and use `syke ask`
or the timeline only when useful.

Agent behavior rules:

- Do not rerun setup blindly after `status=complete`.
- Use `next_steps` as the source of truth.
- Use `syke status --json` for current state.
- Use `syke doctor --json` for repair guidance; it exits non-zero when any
  check fails.
- Use `syke web` or `/api/health` only for observation; the timeline API is
  read-only.

If you want a one-command non-interactive bootstrap from this repo, use:

```bash
bash install_syke.sh
```

Provider auth can be passed via env for agent runners:

```bash
SYKE_PROVIDER=openai \
SYKE_API_KEY=<KEY> \
SYKE_MODEL=gpt-5.4 \
bash install_syke.sh
```

`install_syke.sh` defaults to the real user path: after provider auth is ready,
setup starts background sync. Set `SYKE_SKIP_DAEMON=1` only for CI, tests, or
throwaway profiles.

## First Sync And Onboarding

First sync is not just "fetch recent messages." It is a stitching pass:

1. Detect selected harness roots/files.
2. Read available event traces per harness.
3. Synthesize a coherent cross-harness state.
4. Commit durable memory to `~/.syke/syke.db`.
5. Export current projection to `~/.syke/MEMEX.md`.

The setup receipt is written to `~/.syke/onboarding.json` and surfaced by the
local timeline. It exists so a fresh user does not stare at an empty timeline
and think setup failed. It is not a separate onboarding page; it renders inside
the normal timeline view until real cycles start landing.

Timeline states:

- **MEMEX is bootstrapping** — setup started background synthesis. The user can
  keep working and check back later.
- **MEMEX bootstrap is waiting** — setup is complete, but daemon/sync is not
  currently running. Run `syke sync` once or `syke daemon start`.
- **No harness history detected yet** — Syke did not find prior local traces.
  This is not a failure; future harness activity and `syke record` can still
  create memory.

Fresh-machine timing depends mostly on detected source volume. The agent output
already includes a conservative estimate:

- `estimated_minutes = max(2, total_files // 1500 + 3)`

Observed clean-room runs with near-empty local history:

- first synthesis can finish in under a minute

Heavier histories can take several minutes on first pass. During this window:

- `syke status --json` shows daemon/runtime signals
- `syke web --open` shows the timeline UI
- when no events are available yet, the UI shows first-run setup/synthesis
  state inside the normal timeline view until cycles start landing
- `syke ask` can still run before MEMEX exists, but answer quality improves as
  synthesis completes

If provider/model setup is missing, setup should stop at `needs_provider`.
If someone starts the daemon anyway, the daemon backs off on configuration
errors instead of writing failed cycles every few seconds.

## Fresh Install/Setup Test Without Touching Real Data

Use a separate HOME so your real `~/.syke` is untouched:

```bash
FRESH_HOME="$HOME/.syke-fresh-home"
rm -rf "$FRESH_HOME"
mkdir -p "$FRESH_HOME"

HOME="$FRESH_HOME" uv tool install syke
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh setup --agent
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh status --json
```

If provider auth is already available in that fresh profile:

```bash
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh setup --agent --skip-daemon
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh sync
HOME="$FRESH_HOME" "$FRESH_HOME/.local/bin/syke" --user fresh ask --json "what am I working on"
```

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
- `onboarding.json`

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
- `syke daemon status` should distinguish process, launchd/systemd registration,
  IPC reachability, and warm runtime binding.
- `syke doctor` should explain actionable failures instead of hiding them behind
  a generic unhealthy state.

## Daemon Behavior

On macOS, `syke daemon start` uses launchd. On Linux, it uses a user systemd
service. On other systems, run the daemon manually with `syke daemon run`.

macOS persistence contract:

- launchd plist uses `RunAtLoad`
- launchd plist uses `KeepAlive`
- launchd restarts Syke if the daemon exits unexpectedly
- the daemon process also serves the local timeline UI while running

Linux persistence contract:

- user systemd unit starts the daemon with `Restart=always`
- the daemon process also serves the local timeline UI while running
- legacy cron entries are treated as scheduled sync only, not daemon liveness

Other non-macOS contract:

- `syke daemon run` is the supported foreground path
- no resident timeline-server guarantee is claimed unless a daemon process is actually running

The daemon is intentionally conservative:

- start should not report success unless the runtime is actually reachable
- stop should report incomplete shutdown if the process survives
- self-update should abort if the running daemon cannot be stopped safely
- daemon health treats runtime reachability as critical
- configuration failures should back off instead of hot-looping

## Product QA Checklist

Before a release, verify these from a clean or isolated profile:

- `syke setup --agent` returns `needs_provider` with a non-zero auth exit when no provider exists.
- `syke setup --agent --skip-daemon` completes without launchd/systemd side effects when provider auth exists.
- `syke status --json` includes daemon, runtime, provider, and persistence fields.
- `syke web` serves the normal timeline shell even before the first MEMEX exists.
- `syke daemon run` or `syke daemon start` does not hot-loop when provider/model config is missing.
- `scripts/fresh-install-test.sh --run` passes without touching the real `~/.syke`.
- `scripts/release-preflight.sh` passes before tagging.

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

- `needs_runtime` from `syke setup --agent`: install Node.js 20+ (22 LTS recommended).
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
