#!/usr/bin/env bash
# Agent-first fresh setup smoke in an isolated HOME.
#
# Usage:
#   bash scripts/fresh-install-test.sh --run
#   bash scripts/fresh-install-test.sh --run --wheel dist/syke-0.5.6-py3-none-any.whl
#   bash scripts/fresh-install-test.sh --run --provider-state "$HOME/.syke/pi-agent"
#   bash scripts/fresh-install-test.sh --run --allow-needs-runtime

set -euo pipefail

RUN=false
WHEEL_PATH=""
PROVIDER_STATE=""
USER_ID="fresh"
ALLOW_NEEDS_RUNTIME=false

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run)
      RUN=true
      shift
      ;;
    --wheel)
      WHEEL_PATH="${2:-}"
      shift 2
      ;;
    --provider-state)
      PROVIDER_STATE="${2:-}"
      shift 2
      ;;
    --user)
      USER_ID="${2:-}"
      shift 2
      ;;
    --allow-needs-runtime)
      ALLOW_NEEDS_RUNTIME=true
      shift
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$RUN" != true ]]; then
  cat <<'EOF'
dry-run mode.
re-run with:
  bash scripts/fresh-install-test.sh --run

optional:
  --wheel <path-to-wheel>
  --provider-state <dir-with-auth.json>
  --user <id>
  --allow-needs-runtime
EOF
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required" >&2
  exit 1
fi

if [[ -n "$WHEEL_PATH" && ! -f "$WHEEL_PATH" ]]; then
  echo "wheel not found: $WHEEL_PATH" >&2
  exit 1
fi

if [[ -n "$PROVIDER_STATE" && ! -d "$PROVIDER_STATE" ]]; then
  echo "provider state directory not found: $PROVIDER_STATE" >&2
  exit 1
fi

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

FRESH_HOME="$TMP_ROOT/home"
mkdir -p "$FRESH_HOME"

export HOME="$FRESH_HOME"
export XDG_CONFIG_HOME="$FRESH_HOME/.config"
export XDG_DATA_HOME="$FRESH_HOME/.local/share"
export UV_TOOL_DIR="$FRESH_HOME/.local/uv-tools"
export UV_TOOL_BIN_DIR="$FRESH_HOME/.local/bin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"

echo "[fresh-agent] isolated HOME: $FRESH_HOME"
if [[ -n "$WHEEL_PATH" ]]; then
  echo "[fresh-agent] installing wheel: $WHEEL_PATH"
  uv tool install --force "$WHEEL_PATH" >/dev/null
else
  echo "[fresh-agent] installing current checkout"
  uv tool install --force --directory "$REPO_DIR" . >/dev/null
fi

SYKE_BIN="$UV_TOOL_BIN_DIR/syke"
if [[ ! -x "$SYKE_BIN" ]]; then
  echo "syke binary missing at $SYKE_BIN" >&2
  exit 1
fi

AGENT_JSON_1="$TMP_ROOT/setup-agent-1.json"
AGENT_EXIT_1="$TMP_ROOT/setup-agent-1.exit"
STATUS_JSON="$TMP_ROOT/status.json"

echo "[fresh-agent] status before setup"
"$SYKE_BIN" --user "$USER_ID" status --json >"$STATUS_JSON"
set +e
"$SYKE_BIN" --user "$USER_ID" setup --agent >"$AGENT_JSON_1"
agent_exit_1=$?
set -e
echo "$agent_exit_1" >"$AGENT_EXIT_1"

python3 - "$AGENT_JSON_1" "$AGENT_EXIT_1" "$ALLOW_NEEDS_RUNTIME" <<'PY'
import json
import sys

setup_path, exit_path, allow_runtime = sys.argv[1:4]

with open(setup_path, encoding="utf-8") as fh:
    payload = json.load(fh)
with open(exit_path, encoding="utf-8") as fh:
    code = int(fh.read().strip())

allowed = {"needs_provider", "complete"}
if allow_runtime == "true":
    allowed.add("needs_runtime")
assert payload["status"] in allowed, payload
assert payload["exit_code"] in {0, 1, 2, 3}, payload
assert code == payload["exit_code"], (code, payload["exit_code"], payload)
print(f"[fresh-agent] step1 status={payload['status']} exit={code}")
PY

if [[ -n "$PROVIDER_STATE" ]]; then
  AGENT_JSON_2="$TMP_ROOT/setup-agent-2.json"
  AGENT_EXIT_2="$TMP_ROOT/setup-agent-2.exit"
  ASK_JSON="$TMP_ROOT/ask.json"

  echo "[fresh-agent] copying provider state fixture"
  mkdir -p "$HOME/.syke/pi-agent"
  cp "$PROVIDER_STATE/auth.json" "$HOME/.syke/pi-agent/auth.json"
  if [[ -f "$PROVIDER_STATE/settings.json" ]]; then
    cp "$PROVIDER_STATE/settings.json" "$HOME/.syke/pi-agent/settings.json"
  fi
  if [[ -f "$PROVIDER_STATE/models.json" ]]; then
    cp "$PROVIDER_STATE/models.json" "$HOME/.syke/pi-agent/models.json"
  fi

  echo "[fresh-agent] setup --agent --skip-daemon"
  set +e
  "$SYKE_BIN" --user "$USER_ID" setup --agent --skip-daemon >"$AGENT_JSON_2"
  agent_exit_2=$?
  set -e
  echo "$agent_exit_2" >"$AGENT_EXIT_2"

  python3 - "$AGENT_JSON_2" "$AGENT_EXIT_2" <<'PY'
import json
import sys

setup_path, exit_path = sys.argv[1:3]

with open(setup_path, encoding="utf-8") as fh:
    payload = json.load(fh)
with open(exit_path, encoding="utf-8") as fh:
    code = int(fh.read().strip())

assert payload["status"] == "complete", payload
assert payload["daemon"] == "skipped", payload
assert payload["exit_code"] == 0, payload
assert code == 0, code
assert "daemon start was skipped" in payload.get("instructions", ""), payload
assert payload.get("next_steps", [None])[0] == "syke sync", payload
print("[fresh-agent] step2 complete with skip-daemon contract")
PY

  echo "[fresh-agent] sync + ask smoke"
  "$SYKE_BIN" --user "$USER_ID" sync >/dev/null
  "$SYKE_BIN" --user "$USER_ID" ask --json "what am I working on" >"$ASK_JSON"
  python3 - "$ASK_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)

assert payload["ok"] is True, payload
assert "answer" in payload and payload["answer"], payload
print("[fresh-agent] ask smoke ok")
PY
fi

echo "[fresh-agent] passed"
