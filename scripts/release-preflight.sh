#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "[preflight] repo: $REPO_DIR"
cd "$REPO_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required for release preflight" >&2
  exit 1
fi

echo "[preflight] targeted ruff"
uv run ruff check \
  syke/cli.py \
  syke/daemon/daemon.py \
  syke/daemon/ipc.py \
  syke/runtime/locator.py \
  syke/llm/pi_client.py \
  tests/test_cli.py \
  tests/test_daemon.py \
  tests/test_daemon_ipc.py \
  tests/test_install_surface.py \
  tests/test_pi_client.py \
  tests/test_runtime_locator.py \
  tests/test_runtime_parity.py

echo "[preflight] targeted install/runtime tests"
uv run pytest \
  tests/test_install_surface.py \
  tests/test_runtime_locator.py \
  tests/test_pi_client.py \
  tests/test_daemon.py \
  tests/test_daemon_ipc.py \
  tests/test_runtime_parity.py \
  -q

echo "[preflight] targeted CLI release-path tests"
uv run pytest tests/test_cli.py -q -k \
  'daemon_start_invokes_install or daemon_stop_cleans_stale_launchd_registration or install_current_uses_uv_and_restarts_daemon or self_update_handles_current_or_network_cases or self_update_exits_early_for_source_and_uvx or self_update_runs_upgrade_command_for_install_method or self_update_restarts_daemon_when_previously_running or setup_requires_confirmation_before_mutating or setup_can_decline_background_sync_after_review or setup_auto_installs_managed_build_for_blocked_mac_daemon'

echo "[preflight] build wheel"
rm -rf dist
uv run python -m build

WHEEL_PATH="$(ls dist/*.whl)"
echo "[preflight] smoke artifact install: $WHEEL_PATH"
bash "$SCRIPT_DIR/smoke-artifact-install.sh" "$WHEEL_PATH"

echo "[preflight] smoke isolated uv tool install"
bash "$SCRIPT_DIR/smoke-tool-install.sh"

echo "[preflight] passed"
