# Scripts Surface

This directory contains maintainer scripts. These are not end-user CLI commands.

## Release-Critical Scripts

| Script | Why it is release-critical | Current usage |
|---|---|---|
| `scripts/check_release_tag.py` | Enforces git tag/version alignment before publish. | Called by `.github/workflows/publish.yml`. |
| `scripts/release-candidate.sh` | Freezes and proves a local release candidate before push/tag: clean tree, preflight, local timeline API, optional Linux gates, optional tag check. | Manual pre-push gate. |
| `scripts/smoke-artifact-install.sh` | Validates a built wheel in an isolated venv and checks core JSON command surfaces. | Called by `.github/workflows/ci.yml` and `.github/workflows/publish.yml`. |
| `scripts/release-preflight.sh` | Local maintainer preflight for release readiness (ruff, targeted tests, build, wheel smoke, tool smoke). | Manual local release run; not called by GitHub Actions. |
| `scripts/smoke-tool-install.sh` | Verifies isolated `uv tool install` behavior for the current checkout. | Called by `scripts/release-preflight.sh`. |
| `scripts/linux-product-qa.sh` | Dockerized Linux product QA for the built wheel: install, setup, sync/ask, daemon/web API, and Chromium visualizer checks. | Manual Linux artifact/UI gate before push/tag. |
| `scripts/linux-managed-service-smoke.sh` | Real Linux user-systemd smoke for the public `syke daemon start` contract: systemd registration, resident process, IPC, timeline API, status, and doctor agreement. | Manual Azure/Linux managed-service gate before release. |

## Internal/Dev-Only Scripts

| Script | Purpose |
|---|---|
| `scripts/fresh-install-test.sh` | Agent-first fresh install/setup smoke in isolated HOME; validates `setup --agent` contract and optional provider-backed `sync`/`ask` path. |
| `scripts/dev-reset.sh` | Local reset utility (state, daemon files, optional tool uninstall). |
| `scripts/test-and-monitor.sh` | Local convenience wrapper for lint/tests/build/status/log tailing. |
| `scripts/autoresearch_status.py` | OMX/autoresearch local state inspection utility. |

## Operator Notes

- Keep release-critical scripts stable and backwards-compatible with existing CI/publish workflows.
- Treat internal/dev-only scripts as non-product tooling; avoid documenting them on the main front-door path.
- Do not push, tag, or publish first and call that testing. Run
  `scripts/release-candidate.sh` locally, then use GitHub Actions as a second
  confirmation layer.

## Release Order

1. Start from a clean tree and run `scripts/release-candidate.sh`.
2. Push only after the local candidate gate passes.
3. Wait for GitHub Actions to pass on the exact pushed commit.
4. Bump the package version and changelog for the release.
5. Run `scripts/release-candidate.sh --for-tag vX.Y.Z`.
6. Create and push the tag only after the tag candidate gate passes.
7. Let the publish workflow run `scripts/check_release_tag.py`, build, smoke
   install, and publish from the tagged artifact.
