# Scripts Surface

This directory contains maintainer scripts. These are not end-user CLI commands.

## Release-Critical Scripts

| Script | Why it is release-critical | Current usage |
|---|---|---|
| `scripts/check_release_tag.py` | Enforces git tag/version alignment before publish. | Called by `.github/workflows/publish.yml`. |
| `scripts/smoke-artifact-install.sh` | Validates a built wheel in an isolated venv and checks core JSON command surfaces. | Called by `.github/workflows/ci.yml` and `.github/workflows/publish.yml`. |
| `scripts/release-preflight.sh` | Local maintainer preflight for release readiness (ruff, targeted tests, build, wheel smoke, tool smoke). | Manual local release run; not called by GitHub Actions. |
| `scripts/smoke-tool-install.sh` | Verifies isolated `uv tool install` behavior for the current checkout. | Called by `scripts/release-preflight.sh`. |

## Internal/Dev-Only Scripts

| Script | Purpose |
|---|---|
| `scripts/fresh-install-test.sh` | Dry-run or manual fresh-install checklist for local environments. |
| `scripts/dev-reset.sh` | Local reset utility (state, daemon files, optional tool uninstall). |
| `scripts/test-and-monitor.sh` | Local convenience wrapper for lint/tests/build/status/log tailing. |
| `scripts/autoresearch_status.py` | OMX/autoresearch local state inspection utility. |
| `scripts/eval_karpathy_market_topology.py` | Research scoring helper over `research/` memo content. |

## Operator Notes

- Keep release-critical scripts stable and backwards-compatible with existing CI/publish workflows.
- Treat internal/dev-only scripts as non-product tooling; avoid documenting them on the main front-door path.
