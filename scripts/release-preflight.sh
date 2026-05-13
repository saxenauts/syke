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

echo "[preflight] lockfile and diff hygiene"
uv lock --check
git diff --check
git diff --cached --check

untracked_files="$(git ls-files --others --exclude-standard)"
if [[ -n "$untracked_files" ]]; then
  echo "[preflight] untracked files would be omitted from a release commit:" >&2
  printf '%s\n' "$untracked_files" >&2
  echo "[preflight] add intentional files or update .gitignore before release." >&2
  exit 1
fi

PYTHON_BIN="$(uv run python - <<'PY'
import sys

print(sys.executable)
PY
)"
export PYTHON_BIN

echo "[preflight] targeted ruff"
uv run ruff check \
  syke/entrypoint.py \
  syke/cli_support \
  syke/cli_commands \
  syke/daemon/daemon.py \
  syke/daemon/ipc.py \
  syke/daemon/web.py \
  syke/runtime/locator.py \
  syke/runtime/sandbox.py \
  syke/llm/backends/pi_synthesis.py \
  syke/llm/pi_client.py \
  syke/source_selection.py \
  tests/test_build_prompt.py \
  tests/test_daemon_metrics.py \
  tests/test_pi_state.py \
  tests/test_llm.py \
  tests/test_config_file.py \
  tests/test_azure_gpt5_thinking.py \
  tests/test_cli_contract.py \
  tests/test_daemon_controls.py \
  tests/test_daemon.py \
  tests/test_daemon_ipc.py \
  tests/test_install_surface.py \
  tests/test_web_server.py \
  tests/test_sandbox.py \
  tests/test_pi_synthesis_contract.py \
  tests/test_pi_native_cli.py \
  tests/test_pi_client.py \
  tests/test_source_selection.py \
  tests/test_runtime_locator.py \
  tests/test_runtime_parity.py

echo "[preflight] targeted install/runtime tests"
install_runtime_tests=(
  tests/test_pi_state.py
  tests/test_llm.py
  tests/test_config_file.py
  tests/test_azure_gpt5_thinking.py
  tests/test_install_surface.py
  tests/test_runtime_locator.py
  tests/test_pi_client.py
  tests/test_source_selection.py
  tests/test_build_prompt.py
  tests/test_daemon_metrics.py
  tests/test_daemon.py
  tests/test_daemon_ipc.py
  tests/test_web_server.py
  tests/test_sandbox.py
  tests/test_pi_synthesis_contract.py
  tests/test_runtime_parity.py
)
uv run pytest "${install_runtime_tests[@]}" -q

echo "[preflight] targeted CLI release-path tests"
cli_release_tests=(
  tests/test_cli_contract.py
  tests/test_daemon_controls.py
  tests/test_pi_native_cli.py
)
uv run pytest "${cli_release_tests[@]}" -q

echo "[preflight] build wheel"
rm -rf dist
uv run python -m build

echo "[preflight] package metadata"
uv run --with twine twine check dist/*

WHEEL_PATH="$(uv run python - <<'PY'
from pathlib import Path
wheels = sorted(Path('dist').glob('*.whl'))
if not wheels:
    raise SystemExit('no wheel built in dist/')
print(wheels[0].resolve())
PY
)"
echo "[preflight] smoke artifact install: $WHEEL_PATH"
bash "$SCRIPT_DIR/smoke-artifact-install.sh" "$WHEEL_PATH"

SDIST_PATH="$(uv run python - <<'PY'
from pathlib import Path
sdists = sorted(Path('dist').glob('*.tar.gz'))
if not sdists:
    raise SystemExit('no sdist built in dist/')
print(sdists[0].resolve())
PY
)"
echo "[preflight] smoke sdist install: $SDIST_PATH"
bash "$SCRIPT_DIR/smoke-artifact-install.sh" "$SDIST_PATH"

echo "[preflight] smoke isolated uv tool install"
bash "$SCRIPT_DIR/smoke-tool-install.sh"

echo "[preflight] fresh agent setup smoke"
bash "$SCRIPT_DIR/fresh-install-test.sh" --run --allow-needs-runtime

echo "[preflight] passed"
