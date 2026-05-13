#!/usr/bin/env bash
# Linux end-to-end release QA for the built Syke artifact.
#
# Runs a disposable Linux container, installs the wheel, exercises the agent
# setup/sync/ask path, starts the local timeline server, and drives the HTML UI
# with Chromium through Playwright. This is intentionally manual/opt-in because
# it needs Docker and, for the full path, local provider state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE="${SYKE_LINUX_QA_IMAGE:-mcr.microsoft.com/playwright/python:v1.56.0-noble}"
NODE_MAJOR="${SYKE_LINUX_QA_NODE_MAJOR:-22}"
WHEEL_PATH=""
PROVIDER_STATE=""
USER_ID="linux-qa"
ALLOW_NO_PROVIDER=false
OUTPUT_DIR=""

usage() {
  cat <<'EOF'
usage: scripts/linux-product-qa.sh --wheel dist/syke-0.5.6-py3-none-any.whl --provider-state "$HOME/.syke/pi-agent"

optional:
  --user <id>               user id inside the disposable Linux profile
  --output-dir <dir>        host directory for screenshots/logs
  --allow-no-provider       run only no-provider setup/web smoke

environment:
  SYKE_LINUX_QA_IMAGE       container image, default mcr.microsoft.com/playwright/python:v1.56.0-noble
  SYKE_LINUX_QA_NODE_MAJOR  Node.js major to install in Linux QA, default 22
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --allow-no-provider)
      ALLOW_NO_PROVIDER=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for Linux product QA" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "docker daemon is not reachable" >&2
  exit 1
fi

if [[ -z "$WHEEL_PATH" ]]; then
  wheels=("$REPO_DIR"/dist/*.whl)
  if [[ ${#wheels[@]} -eq 1 && -f "${wheels[0]}" ]]; then
    WHEEL_PATH="${wheels[0]}"
  else
    echo "pass --wheel <path>; no single wheel found in dist/" >&2
    exit 1
  fi
fi

if [[ ! -f "$WHEEL_PATH" ]]; then
  echo "wheel not found: $WHEEL_PATH" >&2
  exit 1
fi

if [[ -z "$PROVIDER_STATE" && "$ALLOW_NO_PROVIDER" != true ]]; then
  echo "full Linux product QA requires --provider-state <dir>; use --allow-no-provider for partial smoke only" >&2
  exit 1
fi

if [[ -n "$PROVIDER_STATE" ]]; then
  if [[ ! -d "$PROVIDER_STATE" ]]; then
    echo "provider state directory not found: $PROVIDER_STATE" >&2
    exit 1
  fi
  if [[ ! -f "$PROVIDER_STATE/auth.json" ]]; then
    echo "provider state is missing auth.json: $PROVIDER_STATE" >&2
    exit 1
  fi
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$REPO_DIR/.playwright-mcp/linux-qa-$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$OUTPUT_DIR"

WHEEL_ABS="$(python3 - "$WHEEL_PATH" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
WHEEL_BASENAME="$(basename "$WHEEL_ABS")"

docker_args=(
  run
  --rm
  --init
  -i
  --ipc=host
  -e "SYKE_QA_USER=$USER_ID"
  -e "SYKE_QA_ALLOW_NO_PROVIDER=$ALLOW_NO_PROVIDER"
  -e "SYKE_QA_WHEEL=/tmp/$WHEEL_BASENAME"
  -e "SYKE_QA_NODE_MAJOR=$NODE_MAJOR"
  -v "$REPO_DIR:/repo:ro"
  -v "$WHEEL_ABS:/tmp/$WHEEL_BASENAME:ro"
  -v "$OUTPUT_DIR:/qa-output"
)

if [[ -n "$PROVIDER_STATE" ]]; then
  PROVIDER_ABS="$(python3 - "$PROVIDER_STATE" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
  docker_args+=(-e "SYKE_QA_PROVIDER_STATE=/provider-state" -v "$PROVIDER_ABS:/provider-state:ro")
fi

echo "[linux-qa] image: $IMAGE"
echo "[linux-qa] wheel: $WHEEL_ABS"
echo "[linux-qa] output: $OUTPUT_DIR"
echo "[linux-qa] node: $NODE_MAJOR.x"

docker "${docker_args[@]}" "$IMAGE" bash -s <<'BASH'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export PIP_ROOT_USER_ACTION=ignore

echo "[linux-qa] platform: $(uname -a)"
echo "[linux-qa] installing Linux QA prerequisites"
apt-get update -qq >/dev/null
apt-get install -y -qq ca-certificates curl gnupg python3-venv >/dev/null
curl -fsSL "https://deb.nodesource.com/setup_${SYKE_QA_NODE_MAJOR}.x" | bash - >/dev/null
apt-get install -y -qq nodejs >/dev/null

TMP_ROOT="$(mktemp -d)"
DAEMON_PID=""
cleanup() {
  if [[ -n "$DAEMON_PID" ]]; then
    kill "$DAEMON_PID" >/dev/null 2>&1 || true
    wait "$DAEMON_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

export HOME="$TMP_ROOT/home"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_DATA_HOME="$HOME/.local/share"
export TMPDIR="$TMP_ROOT/tmp"
export SYKE_WEB_PORT="8767"
mkdir -p "$HOME" "$TMPDIR"

python3 -m venv "$TMP_ROOT/venv"
"$TMP_ROOT/venv/bin/python" -m pip install --quiet --upgrade pip
"$TMP_ROOT/venv/bin/pip" install --quiet "$SYKE_QA_WHEEL" playwright==1.56.0
SYKE_BIN="$TMP_ROOT/venv/bin/syke"

echo "[linux-qa] installed: $("$SYKE_BIN" --version)"
"$SYKE_BIN" --help >/qa-output/syke-help.txt
"$SYKE_BIN" --user "$SYKE_QA_USER" auth status --json >/qa-output/auth-before.json
"$SYKE_BIN" --user "$SYKE_QA_USER" status --json >/qa-output/status-before.json

set +e
"$SYKE_BIN" --user "$SYKE_QA_USER" setup --agent >/qa-output/setup-no-provider.json </dev/null
setup_no_provider_exit=$?
set -e
python3 - "$setup_no_provider_exit" <<'PY'
import json
import sys

code = int(sys.argv[1])
with open("/qa-output/setup-no-provider.json", encoding="utf-8") as fh:
    payload = json.load(fh)

assert payload["status"] in {"needs_provider", "complete"}, payload
assert code == payload["exit_code"], (code, payload)
PY

if [[ -n "${SYKE_QA_PROVIDER_STATE:-}" ]]; then
  echo "[linux-qa] copying provider state fixture"
  mkdir -p "$HOME/.syke/pi-agent"
  cp "$SYKE_QA_PROVIDER_STATE/auth.json" "$HOME/.syke/pi-agent/auth.json"
  for optional in settings.json models.json; do
    if [[ -f "$SYKE_QA_PROVIDER_STATE/$optional" ]]; then
      cp "$SYKE_QA_PROVIDER_STATE/$optional" "$HOME/.syke/pi-agent/$optional"
    fi
  done

  echo "[linux-qa] provider-backed setup"
  "$SYKE_BIN" --user "$SYKE_QA_USER" setup --agent --skip-daemon \
    >/qa-output/setup-provider.json </dev/null
  python3 - <<'PY'
import json

with open("/qa-output/setup-provider.json", encoding="utf-8") as fh:
    payload = json.load(fh)

assert payload["status"] == "complete", payload
assert payload["exit_code"] == 0, payload
assert payload["daemon"] == "skipped", payload
assert payload["next_steps"][0] == "syke sync", payload
PY

  echo "[linux-qa] provider-backed sync"
  "$SYKE_BIN" --user "$SYKE_QA_USER" sync --json >/qa-output/sync.json </dev/null
  python3 - <<'PY'
import json

with open("/qa-output/sync.json", encoding="utf-8") as fh:
    payload = json.load(fh)

assert payload["ok"] is True, payload
assert payload["status"] == "completed", payload
assert payload["trace_id"], payload
PY

  echo "[linux-qa] provider-backed ask"
  "$SYKE_BIN" --user "$SYKE_QA_USER" ask --json "what am I working on" \
    >/qa-output/ask.json </dev/null
  python3 - <<'PY'
import json

with open("/qa-output/ask.json", encoding="utf-8") as fh:
    payload = json.load(fh)

assert payload["ok"] is True, payload
assert payload.get("answer"), payload
PY
else
  if [[ "$SYKE_QA_ALLOW_NO_PROVIDER" != true ]]; then
    echo "provider state missing and partial mode not allowed" >&2
    exit 1
  fi
fi

echo "[linux-qa] daemon + web"
"$SYKE_BIN" --user "$SYKE_QA_USER" daemon run --interval 300 \
  >/qa-output/daemon.log 2>&1 </dev/null &
DAEMON_PID=$!

python3 - <<'PY'
import json
import time
import urllib.request

base = "http://127.0.0.1:8767"
last = None
for _ in range(120):
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as response:
            health = json.load(response)
        with urllib.request.urlopen(f"{base}/api/timeline?days=30", timeout=5) as response:
            timeline = json.load(response)
        with open("/qa-output/web-health.json", "w", encoding="utf-8") as fh:
            json.dump(health, fh, indent=2)
        with open("/qa-output/web-timeline.json", "w", encoding="utf-8") as fh:
            json.dump(timeline, fh, indent=2)
        if health.get("db_present") is True:
            break
    except Exception as exc:
        last = exc
    time.sleep(1)
else:
    raise SystemExit(f"web API did not become ready: {last}")
PY

echo "[linux-qa] browser visualizer"
"$TMP_ROOT/venv/bin/python" - <<'PY'
from pathlib import Path
from playwright.sync_api import sync_playwright

base = "http://127.0.0.1:8767"
out = Path("/qa-output")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("text=syke", timeout=10000)
    body = page.locator("body").inner_text(timeout=10000)
    assert "timeline" in body.lower(), body[:1000]
    assert "Memex" in body, body[:1000]
    assert "Memory" in body, body[:1000]
    assert "Trace" in body, body[:1000]
    page.screenshot(path=str(out / "timeline-1280.png"), full_page=True)

    page.set_viewport_size({"width": 900, "height": 560})
    page.get_by_text("Memory", exact=True).click()
    page.wait_for_timeout(500)
    body_small = page.locator("body").inner_text(timeout=10000)
    assert "Memory" in body_small, body_small[:1000]
    page.screenshot(path=str(out / "timeline-900-memory.png"), full_page=True)

    health = page.evaluate("fetch('/api/health').then(r => r.json())")
    timeline = page.evaluate("fetch('/api/timeline?days=30').then(r => r.json())")
    assert health["db_present"] is True, health
    assert "events" in timeline, timeline
    browser.close()
PY

"$SYKE_BIN" --user "$SYKE_QA_USER" status --json >/qa-output/status-after.json
echo "[linux-qa] passed"
BASH

echo "[linux-qa] passed; artifacts in $OUTPUT_DIR"
