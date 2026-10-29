# Syke Setup Guide

Canonical first-run path for humans and agents.

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
4. Let setup start the background service/bootstrap unless intentionally skipped.
5. On macOS, allow the protected folders Syke should observe when prompted.
6. Open the timeline and keep working while first synthesis runs.

A healthy first run should end with:

- provider selected and daemon-safe
- detected sources selected or intentionally skipped
- `~/.syke/workspace/syke.db` initialized
- `~/.syke/workspace/MEMEX.md` available
- adapter markdowns installed under `~/.syke/workspace/adapters/`
- protected native sessions and final receipts available under
  `~/.syke/control/{sessions,receipts}/`
- background service install either confirmed or clearly skipped/explained
- macOS protected-folder access verified or clearly reported as blocked
- local timeline available through `syke web`

## Agent Mode (Non-Interactive)

Any terminal agent can install and operate Syke. Native history ingestion is
limited separately to the sources in [PLATFORMS.md](../PLATFORMS.md).

```bash
syke setup --agent
```

`--agent` returns JSON with a `status` field and exact `next_steps`:

- `needs_runtime` - install Node.js 22.19 or newer, then run the returned command
- `needs_provider` - choose from `provider_choices`, then run the matching `auth_options` command
- `complete` - setup finished
- `failed` - inspect the returned `error`

Agent mode is for automation. Humans should normally use plain `syke setup`
because it explains planned writes before applying them.

Agent payload fields that matter for orchestration:

- `status`, `exit_code`, `instructions`, `next_steps`
- `provider_choices` and `auth_options` when provider setup is required
- `estimated_minutes`, `total_files`, `estimate_method`, `sources_ingesting`
- `daemon` (`started` on successful setup)
- `daemon_persistence`
- `filesystem_access`
- `monitor`
- `onboarding`

Recommended automation flow:

1. Run `syke setup --agent` and parse `status`.
2. Follow the returned `next_steps`; they preserve explicit `--source` flags.
3. For `needs_provider`, present `provider_choices` and run the selected
   `auth_options` command. Pi opens the browser or shows a device code. Never
   request credentials in chat or print them.
4. Treat background-service failure as setup failure; there is no foreground-only
   installation mode.

After manual `syke sync`, the JSON payload includes `duration_ms`,
`session_id`, `session_file`, `num_turns`, `model`, `cost_usd`,
`memex_updated`, and `next_steps`. The session fields point to Pi's protected
native record when deeper evidence is needed. Agents should treat that as the
handoff point: if `status` is `completed`, stop setup work, move on with normal
user work, and use `syke ask` or the timeline only when useful.

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
SYKE_MODEL=gpt-5.6-luna \
bash install_syke.sh
```

`install_syke.sh` uses the real setup path: after provider auth is ready, setup
starts and verifies the background service.

## First Sync And Onboarding

First sync is not just "fetch recent messages." It is a stitching pass:

1. Detect the harness roots/files selected for ingestion.
2. Read available event traces per harness.
3. Synthesize a coherent cross-harness state.
4. Commit durable memory to `~/.syke/workspace/syke.db`.
5. Export current projection to `~/.syke/workspace/MEMEX.md`.
6. Keep the native Pi session in `~/.syke/control/sessions/` and write one final
   host verdict to `~/.syke/control/receipts/`.

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
  This is not a failure; future harness activity and records admitted by
  `syke record` give later synthesis new evidence.

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

## Provider Setup

You can let interactive `syke setup` handle provider choice, or configure directly:

```bash
syke auth set openai --api-key <KEY> --model gpt-5.6-luna --use
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

- `workspace/syke.db`
- `workspace/MEMEX.md`
- `workspace/adapters/{source}.md`
- `workspace/artifacts/`
- `workspace/harness/`
- `workspace/scratch/`
- `control/runtime/tmp/`
- `pi-agent/{auth.json,settings.json,models.json}` (host-managed)
- `control/sessions/`
- `control/receipts/`
- `control/records/`
- `control/tokenizers/`
- `control/recovery/`
- `source_selection.json`
- `onboarding.json`

The Pi controller may persist learned artifacts under `workspace/` and
operational files under `control/runtime/`. It can inspect but cannot modify
protected sessions, receipts, records, or recovery state under `control/`.

Daemon/system artifacts:

- `~/.config/syke/daemon.log`
- `~/Library/LaunchAgents/com.syke.daemon.plist` (macOS launchd installs)
- `~/.config/systemd/user/syke-daemon.service` (Linux systemd user installs)

## Upgrading From A Pre-v3 Database

This pre-1.0 release does not migrate databases or workspace layouts from
`0.5.10` and earlier. Back up the old installation root and let setup create the
current schema-v3 layout:

```bash
syke daemon stop
mv ~/.syke ~/.syke.pre-v3-backup
# upgrade Syke using the same installer that originally installed it
syke setup
```

There is no automatic import from that backup. Keep it if historical state
matters; do not copy its database or control files into the new layout.

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
- `syke daemon logs` should show explicit UTC timestamps on daemon-owned log lines.
- `syke doctor` should explain actionable failures instead of hiding them behind
  a generic unhealthy state.
- On macOS, interactive `syke doctor` rechecks protected-folder access through
  the background Syke process. `syke doctor --json` reports the last stored
  result without opening a system prompt.

## Background Service Behavior

Syke presents one background-service contract. The platform manager differs,
but the public state machine is the same: `stopped`, `registered`, `running`,
or `stale`.

On macOS, the manager is launchd. On Linux, the manager is a user systemd
service. On other systems, run the service manually with `syke daemon run`.

macOS persistence contract:

- launchd plist uses `RunAtLoad`
- launchd plist uses `KeepAlive`
- launchd restarts Syke if the daemon exits unexpectedly
- the daemon process also serves the local timeline UI while running

Linux persistence contract:

- user systemd unit starts the daemon with `Restart=always`
- the daemon process also serves the local timeline UI while running
- boot-time persistence requires user linger (`loginctl enable-linger <user>`)

Other non-macOS contract:

- `syke daemon run` is the supported foreground path
- no timeline-server guarantee is claimed unless a daemon process is actually running

The daemon is intentionally conservative:

- start should not report success unless the runtime is actually reachable
- stop should report incomplete shutdown if the process survives
- self-update should abort if the running daemon cannot be stopped safely
- daemon health treats runtime reachability as critical
- configuration failures should back off instead of hot-looping

## macOS Permissions And Sandbox

On macOS, Syke uses `sandbox-exec` around each model-invoked tool process. Pi's
host process remains outside that sandbox so it can use provider credentials
and write its native session. Pi's built-in tools and untrusted extensions are
disabled.

- the current user's full `$HOME`, installed Syke code, and protected
  `control/` state are readable
- ordinary computer files are read-only
- only the Syke workspace and `control/runtime/` are writable
- protected sessions, receipts, records, signals, and recovery state are
  explicitly non-writable
- outbound network is allowed for provider calls

This is separate from macOS privacy permission. Normal folders under `$HOME`
work without an extra step, but Desktop, Documents, and Downloads require user
consent. During setup—or when `syke daemon start` enables background operation
later—Syke starts a temporary one-shot launchd job through the same installed
launcher, Python runtime, Node runtime, and Seatbelt profile used by background
work. That job tries to list each protected folder so macOS can ask for access
and Syke can verify the answer immediately.

The check stores only each folder's granted, denied, or missing status plus the
Python and Node runtime identities. It does not store filenames or file content.
The one-shot job exits after the check; the normal Syke daemon remains the only
persistent background process.

If access is denied, setup still completes because ordinary home folders remain
usable. Enable the folder later in System Settings > Privacy & Security > Files
& Folders, then run `syke doctor` in a terminal. A runtime replacement makes the
stored result stale, so doctor rechecks it. No Apple Developer account or Syke
app bundle is required for this installation-local consent flow.

Linux does not yet have an equivalent model-tool sandbox guarantee in this
release. Setup reports that OS read-only enforcement is unavailable instead of
claiming the macOS boundary is active.

## Troubleshooting

- `needs_runtime` from `syke setup --agent`: install Node.js 22.19 or newer.
- Provider/auth failures: run `syke auth status` then `syke doctor`.
- Empty/old memex: run `syke sync`, then `syke memex`.
- Protected macOS folder reported as blocked: enable it under System Settings >
  Privacy & Security > Files & Folders, then run interactive `syke doctor`.
- `syke ask --json` exits non-zero: read the structured `error` field. Do not
  treat a backend/runtime error as an answer.
- Daemon running but ask path feels stale: run `syke daemon status` and compare
  the warm runtime provider/model with `syke auth status`.

## Related Docs

- [README](../README.md)
- [Providers](PROVIDERS.md)
- [Config Reference](CONFIG_REFERENCE.md)
- [Architecture](ARCHITECTURE.md)
