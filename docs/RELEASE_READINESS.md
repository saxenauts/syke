# Release Readiness Checklist

Maintainer checklist for the `0.5.2` release line. This is intentionally internal-facing:
keep public docs focused on setup and daily use.

Last updated: 2026-04-22.

## Current Status

- Branch: `dev/0.5.2`.
- Version target: `0.5.2`.
- Release posture: hardening, not feature expansion.
- Current validation snapshot: full local test suite passed (`428 passed, 8 skipped`) and
  `scripts/release-preflight.sh` passed on 2026-04-22.

## Recently Changed Contracts

- Ask errors: if the backend reports an error in metadata, `syke ask --json` and
  `syke ask --jsonl` must exit non-zero and emit a structured error instead of a fake answer.
- Daemon health: runtime reachability is now a critical health check alongside Python and database.
- Pi state: `~/.syke/pi-agent` state and migrated auth/settings/model files must be owner-only.
- Pi subprocesses: node/OAuth/runtime child processes must receive bounded environment variables,
  not the full host shell environment.
- Pi sandbox: temporary sandbox profiles must be removed after runtime stop and after launch failure.
- Source selection: corrupt or invalid persisted selections fail closed to an empty tuple, not open to
  all sources.
- Synthesis prompt: the bundled synthesis skill describes a scheduled daemon cycle, not a user ask.
- Rubric bridge: `SYKE_RPC_RUBRIC_SPEC_PATH` can supply a dynamic judge schema; missing or invalid
  specs must fall back to the legacy v1 schema.

## Release Gates

- Public install path works from a built wheel in an isolated environment.
- `uv tool install` smoke path works from the checkout.
- `syke setup`, `syke status`, `syke doctor`, `syke ask`, `syke memex`, `syke record`, and
  daemon commands keep their JSON contracts stable.
- Daemon start/stop/status must be honest about process, registration, IPC, and warm runtime state.
- Source selection must be persisted, visible in status, and respected by daemon/runtime paths.
- Auth/provider flows must not depend on an interactive shell environment after setup.
- Runtime sandbox behavior must be fail-safe and must not leave stale temporary policy files.
- Docs must not expose research-only or replay-lab internals as Syke product surface.
- Release-critical scripts must cover every contract listed in this file.

## Required Local Preflight

Run before tagging:

```bash
bash scripts/release-preflight.sh
```

The preflight should cover:

- ruff on release-path modules and tests
- targeted install/runtime tests
- targeted CLI contract tests
- source-selection and synthesis prompt contract tests
- daemon metrics health contract tests
- wheel build
- wheel smoke install
- isolated `uv tool install` smoke

## Open Loops

- Full test suite should still be run before the final tag, even when targeted preflight passes.
- CI has no dedicated typecheck gate yet.
- Live Pi integration remains opt-in with `SYKE_RUN_PI_INTEGRATION=1`.
- GitHub CI still needs to run after pushing this branch.
