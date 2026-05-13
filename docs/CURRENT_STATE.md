# Current State

Implementation snapshot for the current 0.5.x runtime line.

## Baseline

- Runtime is Pi-native for both `syke ask` and synthesis.
- Canonical workspace is flat at `~/.syke/`.
- Canonical mutable store is `~/.syke/syke.db`.
- Main projections are `~/.syke/MEMEX.md` and `~/.syke/PSYCHE.md`.
- Adapter guides are markdown files at `~/.syke/adapters/{source}.md`.

## Control Surfaces

- Primary CLI: `syke setup`, `syke ask`, `syke memex`, `syke record`, `syke status`, `syke sync`, `syke auth`, `syke doctor`.
- Background loop: daemon (`launchd` on macOS, cron/manual path on other systems).
- Local timeline UI: read-only daemon-served HTML/API bound to loopback.
- Distribution installs Syke capability surfaces into detected harness targets.

## Source Selection Contract

- Persisted selection file: `~/.syke/source_selection.json`.
- Written by setup/sync flows when source selections are explicitly set.
- Read by setup/sync/daemon runtime paths to scope selected-source behavior.
- Invalid persisted selections fail closed to an empty selection instead of silently broadening scope.
- `--source` remains a hidden CLI option used for automation flows.

## Runtime Safety Contracts

- Pi subprocesses receive bounded environment variables instead of the full host shell environment.
- Pi OAuth login passes only provider-relevant credentials and required Syke/Pi state.
- Runtime sandbox profiles are temporary and must be cleaned up after stop or launch failure.
- Daemon health treats runtime reachability as release-critical, not merely informational.
- macOS daemon installs are persistent through launchd `RunAtLoad` + `KeepAlive`.
- Non-macOS cron/manual paths preserve sync cadence but do not provide the same resident timeline-server guarantee.
- Configuration failures back off instead of hot-looping daemon cycles.

## Onboarding Contracts

- Human setup is inspect-then-apply and should leave clear next commands.
- Agent setup is JSON-first and should be driven by `status`, `exit_code`, and `next_steps`.
- `~/.syke/onboarding.json` is a setup receipt for first-run timeline states before the first MEMEX exists.
- Timeline empty states distinguish bootstrapping, waiting for sync/daemon, and no detected harness history.

## Synthesis And Rubric Contracts

- Bundled synthesis instructions describe scheduled memory maintenance cycles, not user ask serving.
- Benchmark judge RPC can load a dynamic rubric schema from `SYKE_RPC_RUBRIC_SPEC_PATH`.
- Missing or invalid rubric specs fall back to the legacy v1 judge schema.

## Known Limits

- CI does not enforce a dedicated typecheck gate yet.
- Live Pi integration tests are opt-in (`SYKE_RUN_PI_INTEGRATION=1`).

## Read Next

- [Setup Guide](SETUP.md)
- [Providers](PROVIDERS.md)
- [Runtime and Replay](RUNTIME_AND_REPLAY.md)
- [Architecture](ARCHITECTURE.md)
- [Scripts Surface](../scripts/README.md)
