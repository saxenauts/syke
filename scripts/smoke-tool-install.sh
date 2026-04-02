#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 0 ]]; then
  echo "usage: $0" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

HOME_DIR="$TMP_ROOT/home"
BIN_DIR="$TMP_ROOT/bin"
TOOL_DIR="$TMP_ROOT/tools"
CACHE_DIR="$TMP_ROOT/cache"
USER_ID="release-smoke"

mkdir -p "$HOME_DIR" "$BIN_DIR"

export HOME="$HOME_DIR"
export XDG_BIN_HOME="$BIN_DIR"
export UV_TOOL_DIR="$TOOL_DIR"
export UV_CACHE_DIR="$CACHE_DIR"

SYKE_BIN="$BIN_DIR/syke"
AUTH_JSON="$TMP_ROOT/auth-status.json"
DOCTOR_JSON="$TMP_ROOT/doctor.json"
STATUS_JSON="$TMP_ROOT/status.json"
SETUP_JSON="$TMP_ROOT/setup.json"
DAEMON_STATUS_JSON="$TMP_ROOT/daemon-status.json"
DAEMON_LOG="$HOME_DIR/.config/syke/daemon.log"

mkdir -p "$(dirname "$DAEMON_LOG")"

echo "[tool-smoke] installing current checkout into isolated uv tool dirs"
uv tool install --force --no-cache --directory "$REPO_DIR" .

if [[ ! -x "$SYKE_BIN" ]]; then
  echo "isolated syke executable not found at $SYKE_BIN" >&2
  exit 1
fi

echo "[tool-smoke] basic CLI commands"
"$SYKE_BIN" --version >/dev/null
"$SYKE_BIN" --help >/dev/null
"$SYKE_BIN" setup --help >/dev/null
"$SYKE_BIN" doctor --help >/dev/null

echo "[tool-smoke] JSON surfaces"
"$SYKE_BIN" --user "$USER_ID" auth status --json >"$AUTH_JSON"
"$SYKE_BIN" --user "$USER_ID" doctor --json >"$DOCTOR_JSON"
"$SYKE_BIN" --user "$USER_ID" status --json >"$STATUS_JSON"
"$SYKE_BIN" --user "$USER_ID" setup --json >"$SETUP_JSON"

"$PYTHON_BIN" - "$AUTH_JSON" "$DOCTOR_JSON" "$STATUS_JSON" "$SETUP_JSON" <<'PY'
import json
import sys

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
assert doctor["ok"] in {True, False}
assert status["ok"] is True
assert setup["ok"] is True
assert setup["mode"] == "inspect"
PY

echo "[tool-smoke] foreground daemon smoke"
"$SYKE_BIN" --user "$USER_ID" daemon run --interval 60 >"$DAEMON_LOG" 2>&1 &
DAEMON_PID=$!
cleanup() {
  kill "$DAEMON_PID" >/dev/null 2>&1 || true
  wait "$DAEMON_PID" >/dev/null 2>&1 || true
}
trap 'cleanup; rm -rf "$TMP_ROOT"' EXIT

for _ in $(seq 1 40); do
  "$SYKE_BIN" --user "$USER_ID" status --json >"$DAEMON_STATUS_JSON" || true
  if "$PYTHON_BIN" - "$DAEMON_STATUS_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)

daemon = payload.get("daemon") or {}
signals = payload.get("runtime_signals") or {}
ipc = signals.get("daemon_ipc") or {}

ok = bool(daemon.get("running")) and bool(ipc.get("socket_present"))
raise SystemExit(0 if ok else 1)
PY
  then
    break
  fi
  sleep 1
done

"$PYTHON_BIN" - "$DAEMON_STATUS_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)

daemon = payload["daemon"]
ipc = payload["runtime_signals"]["daemon_ipc"]

assert daemon["running"] is True, payload
assert ipc["socket_present"] is True, payload
PY

cleanup
trap 'rm -rf "$TMP_ROOT"' EXIT

echo "[tool-smoke] isolated uv tool install passed"
