#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/syke.whl-or-sdist" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
ARTIFACT_PATH="$1"

if [[ ! -f "$ARTIFACT_PATH" ]]; then
  echo "artifact not found: $ARTIFACT_PATH" >&2
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
SETUP_AGENT_JSON="$TMP_ROOT/setup-agent.json"
SETUP_AGENT_EXIT="$TMP_ROOT/setup-agent.exit"

mkdir -p "$HOME_DIR"

echo "[smoke] creating isolated environment"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null

echo "[smoke] installing built artifact"
"$VENV_DIR/bin/pip" install "$ARTIFACT_PATH" >/dev/null

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
set +e
"$SYKE_BIN" doctor --json >"$DOCTOR_JSON"
doctor_exit=$?
set -e
"$SYKE_BIN" status --json >"$STATUS_JSON"
"$SYKE_BIN" setup --json >"$SETUP_JSON"

echo "[smoke] agent setup JSON contract"
set +e
"$SYKE_BIN" setup --agent >"$SETUP_AGENT_JSON"
agent_exit=$?
set -e
printf '%s' "$agent_exit" >"$SETUP_AGENT_EXIT"

echo "[smoke] package assets and clean-install behavior"
"$VENV_DIR/bin/python" - "$AUTH_JSON" "$DOCTOR_JSON" "$STATUS_JSON" "$SETUP_JSON" "$SETUP_AGENT_JSON" "$SETUP_AGENT_EXIT" "$doctor_exit" <<'PY'
import json
import sys
from importlib.resources import files

auth_path, doctor_path, status_path, setup_path, setup_agent_path, setup_agent_exit_path, doctor_exit_raw = sys.argv[1:8]
doctor_exit = int(doctor_exit_raw)

with open(auth_path, encoding="utf-8") as fh:
    auth = json.load(fh)

with open(doctor_path, encoding="utf-8") as fh:
    doctor = json.load(fh)

with open(status_path, encoding="utf-8") as fh:
    status = json.load(fh)

with open(setup_path, encoding="utf-8") as fh:
    setup = json.load(fh)

with open(setup_agent_path, encoding="utf-8") as fh:
    setup_agent = json.load(fh)

with open(setup_agent_exit_path, encoding="utf-8") as fh:
    setup_agent_exit = int(fh.read().strip())

assert auth["ok"] is True
assert auth["selected_provider"]["id"] is None

assert doctor["ok"] is False
assert doctor_exit == 1
assert "provider" in doctor["checks"]
assert "pi_runtime" in doctor["checks"]

assert status["ok"] is True
assert "daemon" in status
assert "runtime_signals" in status

assert setup["ok"] is True
assert setup["mode"] == "inspect"
assert "daemon" in setup
assert "runtime" in setup

assert setup_agent["status"] in {"needs_runtime", "needs_provider", "complete"}
assert setup_agent["exit_code"] in {0, 1, 2, 3}
assert setup_agent_exit == setup_agent["exit_code"]

skill_file = files("syke.llm.backends.skills").joinpath("pi_synthesis.md")
assert skill_file.is_file(), "missing packaged synthesis skill"

distribution_skill = files("syke.distribution").joinpath("SKILL.md")
assert distribution_skill.is_file(), "missing packaged Syke distribution skill"
assert "Node.js 20+ (22 LTS recommended)" in distribution_skill.read_text(encoding="utf-8")

from syke.observe.registry import HarnessRegistry

registry = HarnessRegistry()
assert registry.active_harnesses(), "no active harnesses discovered from packaged Observe catalog"
PY

echo "[smoke] artifact install passed"
