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

echo "[smoke] package assets and clean-install behavior"
"$VENV_DIR/bin/python" - "$AUTH_JSON" "$DOCTOR_JSON" <<'PY'
import json
import sys
from importlib.resources import files

auth_path, doctor_path = sys.argv[1], sys.argv[2]

with open(auth_path, encoding="utf-8") as fh:
    auth = json.load(fh)

with open(doctor_path, encoding="utf-8") as fh:
    doctor = json.load(fh)

assert auth["ok"] is True
assert auth["selected_provider"]["id"] is None
assert "codex" in auth["available_providers"]

assert doctor["ok"] is False
assert "provider" in doctor["checks"]
assert "pi_runtime" in doctor["checks"]

skill_file = files("syke.llm.backends.skills").joinpath("pi_synthesis.md")
assert skill_file.is_file(), "missing packaged synthesis skill"

descriptor_dir = files("syke.observe.descriptors")
descriptor_names = sorted(item.name for item in descriptor_dir.iterdir())
assert "claude-code.toml" in descriptor_names, "missing observe descriptor"

from syke.observe.registry import HarnessRegistry

registry = HarnessRegistry()
assert registry.active_harnesses(), "no active harnesses discovered from packaged descriptors"
PY

echo "[smoke] artifact install passed"
