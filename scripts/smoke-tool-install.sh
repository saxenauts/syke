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
SETUP_AGENT_JSON="$TMP_ROOT/setup-agent.json"
SETUP_AGENT_EXIT="$TMP_ROOT/setup-agent.exit"
DAEMON_STATUS_JSON="$TMP_ROOT/daemon-status.json"
DAEMON_LOG="$HOME_DIR/.config/syke/daemon.log"
WEB_HEALTH_JSON="$TMP_ROOT/web-health.json"
WEB_TIMELINE_JSON="$TMP_ROOT/web-timeline.json"
WEB_PORT="$("$PYTHON_BIN" - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"

mkdir -p "$(dirname "$DAEMON_LOG")"
export SYKE_WEB_PORT="$WEB_PORT"

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
set +e
"$SYKE_BIN" --user "$USER_ID" doctor --json >"$DOCTOR_JSON"
doctor_exit=$?
set -e
"$SYKE_BIN" --user "$USER_ID" status --json >"$STATUS_JSON"
"$SYKE_BIN" --user "$USER_ID" setup --json >"$SETUP_JSON"

echo "[tool-smoke] agent setup JSON contract"
set +e
"$SYKE_BIN" --user "$USER_ID" setup --agent >"$SETUP_AGENT_JSON"
agent_exit=$?
set -e
printf '%s' "$agent_exit" >"$SETUP_AGENT_EXIT"

"$PYTHON_BIN" - "$AUTH_JSON" "$DOCTOR_JSON" "$STATUS_JSON" "$SETUP_JSON" "$SETUP_AGENT_JSON" "$SETUP_AGENT_EXIT" "$doctor_exit" <<'PY'
import json
import sys

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
assert doctor["ok"] in {True, False}
assert doctor_exit == (0 if doctor["ok"] else 1)
assert status["ok"] is True
assert setup["ok"] is True
assert setup["mode"] == "inspect"
assert setup_agent["status"] in {"needs_runtime", "needs_provider", "complete"}
assert setup_agent["exit_code"] in {0, 1, 2, 3}
assert setup_agent_exit == setup_agent["exit_code"]
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

for _ in $(seq 1 20); do
  if "$PYTHON_BIN" - "$WEB_PORT" "$WEB_HEALTH_JSON" "$WEB_TIMELINE_JSON" <<'PY'
import json
import sys
import urllib.request

port, health_path, timeline_path = sys.argv[1:4]
base = f"http://127.0.0.1:{port}"

try:
    with urllib.request.urlopen(f"{base}/api/health", timeout=1) as response:
        health = json.load(response)
    with urllib.request.urlopen(f"{base}/api/timeline?days=7", timeout=1) as response:
        timeline = json.load(response)
except Exception:
    raise SystemExit(1)

with open(health_path, "w", encoding="utf-8") as fh:
    json.dump(health, fh)
with open(timeline_path, "w", encoding="utf-8") as fh:
    json.dump(timeline, fh)
PY
  then
    break
  fi
  sleep 1
done

"$PYTHON_BIN" - "$WEB_HEALTH_JSON" "$WEB_TIMELINE_JSON" <<'PY'
import json
import sys

health_path, timeline_path = sys.argv[1:3]

with open(health_path, encoding="utf-8") as fh:
    health = json.load(fh)
with open(timeline_path, encoding="utf-8") as fh:
    timeline = json.load(fh)

assert health["db_present"] is True, health
assert health["setup_blocker"]["kind"] == "provider", health
if health["last_cycle"] is not None:
    assert health["last_cycle"]["status"] == "blocked", health
assert timeline["count"] in {0, 1}, timeline
if timeline["events"]:
    event = timeline["events"][0]
    assert event["kind"] == "cycle", timeline
    assert event["status"] == "blocked", timeline
    assert event["memex_updated"] == 0, timeline
PY

cleanup
trap 'rm -rf "$TMP_ROOT"' EXIT

echo "[tool-smoke] isolated uv tool install passed"
