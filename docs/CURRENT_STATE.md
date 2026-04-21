# Current State

Snapshot of the implementation baseline for Syke `0.5.2`.

This file is intentionally short and operational. Use it as the anchor before deep dives into architecture, setup, or replay docs.

## Baseline

- Runtime is Pi-native for both `syke ask` and synthesis.
- Canonical workspace is `~/.syke/` (flat model, no `data/{user}` nesting).
- Canonical mutable store is `~/.syke/syke.db`.
- Agent identity is `~/.syke/PSYCHE.md`.
- Current memex projection is `~/.syke/MEMEX.md`.
- Observe adapters are markdown guides at `~/.syke/adapters/{source}.md`.

## Control Surfaces

- CLI is the trusted control plane (`syke setup|status|doctor|ask|context|record|sync`).
- Background loop is daemon-driven (launchd on macOS, cron/manual on other systems).
- Distribution installs the Syke capability package into detected harness surfaces.

## Known Limits

- Setup/source selection plumbing still includes hidden `--source` surfaces that are not yet end-to-end filtered in synthesis.
- CI does not yet enforce coverage or a typecheck gate.
- Live Pi integration tests remain opt-in (`SYKE_RUN_PI_INTEGRATION=1`).
- Some architecture boundaries still have path and ownership drift (tracked for cleanup batches).

## Read Next

- [Setup Guide](SETUP.md)
- [Architecture](ARCHITECTURE.md)
- [Runtime and Replay](RUNTIME_AND_REPLAY.md)
- [Config Reference](CONFIG_REFERENCE.md)
