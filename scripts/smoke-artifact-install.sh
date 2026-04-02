#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/syke.whl" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
WHEEL_PATH="$1"

if [[ ! -f "$WHEEL_PATH" ]]; then
  echo "wheel not found: $WHEEL_PATH" >&2
  exit 1
fi

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

VENV_DIR="$TMP_ROOT/venv"
HOME_DIR="$TMP_ROOT/home"
AUTH_JSON="$TMP_ROOT/auth-status.json"
DOCTOR_JSON="$TMP_ROOT/doctor.json"
STATUS_JSON="$TMP_ROOT/status.json"
SETUP_JSON="$TMP_ROOT/setup.json"

mkdir -p "$HOME_DIR"

echo "[smoke] creating isolated environment"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null

echo "[smoke] installing built wheel"
"$VENV_DIR/bin/pip" install "$WHEEL_PATH" >/dev/null

export HOME="$HOME_DIR"
SYKE_BIN="$VENV_DIR/bin/syke"

# Ensure inline Python checks import the installed wheel, not the checkout.
cd "$TMP_ROOT"

echo "[smoke] basic CLI commands"
"$SYKE_BIN" --version >/dev/null
"$SYKE_BIN" --help >/dev/null
"$SYKE_BIN" setup --help >/dev/null
"$SYKE_BIN" auth --help >/dev/null
"$SYKE_BIN" doctor --help >/dev/null

echo "[smoke] JSON status surfaces"
"$SYKE_BIN" auth status --json >"$AUTH_JSON"
"$SYKE_BIN" doctor --json >"$DOCTOR_JSON"
"$SYKE_BIN" status --json >"$STATUS_JSON"
"$SYKE_BIN" setup --json >"$SETUP_JSON"

echo "[smoke] package assets and clean-install behavior"
"$VENV_DIR/bin/python" - "$AUTH_JSON" "$DOCTOR_JSON" "$STATUS_JSON" "$SETUP_JSON" <<'PY'
import json
import sys
from importlib.resources import files

auth_path, doctor_path, status_path, setup_path = sys.argv[1:5]

with open(auth_path, encoding="utf-8") as fh:
    auth = json.load(fh)

with open(doctor_path, encoding="utf-8") as fh:
    doctor = json.load(fh)

with open(status_path, encoding="utf-8") as fh:
    status = json.load(fh)

with open(setup_path, encoding="utf-8") as fh:
    setup = json.load(fh)

assert auth["ok"] is True
assert auth["selected_provider"]["id"] is None

assert doctor["ok"] is False
assert "provider" in doctor["checks"]
assert "pi_runtime" in doctor["checks"]

assert status["ok"] is True
assert "daemon" in status
assert "runtime_signals" in status

assert setup["ok"] is True
assert setup["mode"] == "inspect"
assert "daemon" in setup
assert "runtime" in setup

skill_file = files("syke.llm.backends.skills").joinpath("pi_synthesis.md")
assert skill_file.is_file(), "missing packaged synthesis skill"

from syke.observe.registry import HarnessRegistry

registry = HarnessRegistry()
assert registry.active_harnesses(), "no active harnesses discovered from packaged Observe catalog"
PY

echo "[smoke] artifact install passed"
