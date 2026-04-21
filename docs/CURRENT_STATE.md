# Current State

Implementation snapshot for the current runtime on `main`.

## Baseline

- Runtime is Pi-native for both `syke ask` and synthesis.
- Canonical workspace is flat at `~/.syke/`.
- Canonical mutable store is `~/.syke/syke.db`.
- Main projections are `~/.syke/MEMEX.md` and `~/.syke/PSYCHE.md`.
- Adapter guides are markdown files at `~/.syke/adapters/{source}.md`.

## Control Surfaces

- Primary CLI: `syke setup`, `syke ask`, `syke context`, `syke record`, `syke status`, `syke sync`, `syke auth`, `syke doctor`.
- Background loop: daemon (`launchd` on macOS, cron/manual path on other systems).
- Distribution installs Syke capability surfaces into detected harness targets.

## Source Selection Contract

- Persisted selection file: `~/.syke/source_selection.json`.
- Written by setup/sync flows when source selections are explicitly set.
- Read by setup/sync/daemon runtime paths to scope selected-source behavior.
- `--source` remains a hidden CLI option used for automation flows.

## Known Limits

- CI does not enforce a dedicated typecheck gate yet.
- Live Pi integration tests are opt-in (`SYKE_RUN_PI_INTEGRATION=1`).

## Read Next

- [Setup Guide](SETUP.md)
- [Providers](PROVIDERS.md)
- [Runtime and Replay](RUNTIME_AND_REPLAY.md)
- [Architecture](ARCHITECTURE.md)
