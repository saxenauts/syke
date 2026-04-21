# Syke Setup Guide

Canonical first-run path for the current Syke runtime.

## First-Run Path

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

`syke setup` is inspect-then-apply: it inspects provider/runtime/sources first, shows planned actions, then applies on confirmation.

## Agent Mode (Non-Interactive)

```bash
syke setup --agent
```

`--agent` returns JSON with a `status` field:

- `needs_runtime` - install Node.js 18+ and rerun setup
- `needs_provider` - configure provider auth and rerun setup
- `complete` - setup finished
- `failed` - inspect the returned `error`

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

## Source Selection Contract

Source selection is persisted and reused across setup/sync/daemon flows.

- Interactive `syke setup` prompts for detected sources.
- Automation can pass repeated `--source` values to `syke setup`.
- `syke sync` accepts repeated `--source` values for the same persisted selection flow.
- Selections are stored at `~/.syke/source_selection.json`.
- If no selection exists yet, runtime behavior is unrestricted (`None` selection).

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

## Verify After Setup

```bash
syke status
syke auth status
syke daemon status
syke doctor
```

## Troubleshooting

- `needs_runtime` from `syke setup --agent`: install Node.js 18+.
- Provider/auth failures: run `syke auth status` then `syke doctor`.
- Empty/old memex: run `syke sync`, then `syke context`.
- Background sync unavailable on macOS source checkouts under protected folders: use `syke install-current` and rerun setup.

## Related Docs

- [README](../README.md)
- [Providers](PROVIDERS.md)
- [Config Reference](CONFIG_REFERENCE.md)
- [Runtime And Replay](RUNTIME_AND_REPLAY.md)
- [Scripts Surface](../scripts/README.md)
