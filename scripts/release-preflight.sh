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
  syke/cli_support \
  syke/cli_commands \
  syke/daemon/daemon.py \
  syke/daemon/ipc.py \
  syke/runtime/locator.py \
  syke/llm/pi_client.py \
  tests/test_cli_contract.py \
  tests/test_daemon_controls.py \
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
uv run pytest \
  tests/test_cli_contract.py \
  tests/test_daemon_controls.py \
  -q

echo "[preflight] build wheel"
rm -rf dist
uv run python -m build

WHEEL_PATH="$(python - <<'PY'
from pathlib import Path
wheels = sorted(Path('dist').glob('*.whl'))
if not wheels:
    raise SystemExit('no wheel built in dist/')
print(wheels[0].resolve())
PY
)"
echo "[preflight] smoke artifact install: $WHEEL_PATH"
bash "$SCRIPT_DIR/smoke-artifact-install.sh" "$WHEEL_PATH"

echo "[preflight] smoke isolated uv tool install"
bash "$SCRIPT_DIR/smoke-tool-install.sh"

echo "[preflight] passed"
