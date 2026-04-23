# Release Readiness Checklist

Maintainer checklist for the `0.5.2` release line. This is intentionally internal-facing:
keep public docs focused on setup and daily use.

Last updated: 2026-04-22.

## Current Status

- Branch: `dev/0.5.2`.
- Version target: `0.5.2`.
- Release posture: hardening, not feature expansion.
- Current validation snapshot: full local test suite passed (`430 passed, 8 skipped`),
  live Pi integration passed (`3 passed` against `openai-codex/gpt-5.4`), and
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
- MEMEX budget: exported and injected MEMEX headers use the 2,000-token release cap.
- Rubric bridge: `SYKE_RPC_RUBRIC_SPEC_PATH` can supply a dynamic judge schema; missing or invalid
  specs must fall back to the legacy v1 schema.

## Release Gates

- Public install path works from a built wheel in an isolated environment.
- `uv tool install` smoke path works from the checkout.
- GitHub CI runs on `main`, `dev/**`, and PRs to `main`.
- `syke setup`, `syke status`, `syke doctor`, `syke ask`, `syke memex`, `syke record`, and
  daemon commands keep their JSON contracts stable.
- Daemon start/stop/status must be honest about process, registration, IPC, and warm runtime state.
- Source selection must be persisted, visible in status, and respected by daemon/runtime paths.
- Auth/provider flows must not depend on an interactive shell environment after setup.
- Runtime sandbox behavior must be fail-safe and must not leave stale temporary policy files.
- macOS sandbox claims must stay filesystem-scoped: selected harness roots are read-only, Syke/Pi
  state is writable, outbound network is allowed, and Linux bubblewrap isolation is not claimed.
- Docs must not expose research-only or replay-lab internals as Syke product surface.
- Release-critical scripts must cover every contract listed in this file.

## Remaining Product Risks

- Linux runtime isolation has not been designed to parity with macOS `sandbox-exec`.
- Broad `syke ask` queries can still be slow; healthy runtime does not guarantee short latency for
  open-ended repo/history questions.
- Ask fallback slot accounting is separate from daemon IPC accounting; error text should continue to
  stay explicit that capacity failures refer to direct fallback asks.
- Source selection narrows sandbox/prompt scope, but choosing the semantically right latest evidence
  remains an agent-quality problem, not a solved filesystem problem.

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
- Live Pi integration remains opt-in with `SYKE_RUN_PI_INTEGRATION=1` and
  `SYKE_LIVE_PI_AGENT_DIR=<configured pi-agent dir>`.
- GitHub CI still needs to run after pushing this branch.
