#!/usr/bin/env bash
# Linux managed-service smoke for a real systemd user session.
#
# This is the Linux counterpart to the macOS launchd service proof: it verifies
# the public `syke daemon start` contract, not the foreground debug path.

set -euo pipefail

SYKE_BIN="${SYKE_BIN:-syke}"
USER_ID="${SYKE_SMOKE_USER:-linux-smoke}"
INTERVAL="${SYKE_SMOKE_INTERVAL:-300}"
WEB_PORT="${SYKE_WEB_PORT:-8765}"
OUTPUT_DIR="${SYKE_SMOKE_OUTPUT_DIR:-}"
STOP_AFTER="${SYKE_SMOKE_STOP_AFTER:-0}"
REQUIRE_DOCTOR_OK="${SYKE_SMOKE_REQUIRE_DOCTOR_OK:-0}"

usage() {
  cat <<'EOF'
usage: scripts/linux-managed-service-smoke.sh

environment:
  SYKE_BIN                         syke executable to test, default: syke
  SYKE_SMOKE_USER                  Syke user id, default: linux-smoke
  SYKE_SMOKE_INTERVAL              daemon interval seconds, default: 300
  SYKE_WEB_PORT                    timeline API port, default: 8765
  SYKE_SMOKE_OUTPUT_DIR            artifact directory, default: ./.syke-linux-service-smoke/<timestamp>
  SYKE_SMOKE_STOP_AFTER=1          stop and unload service before exit
  SYKE_SMOKE_REQUIRE_DOCTOR_OK=1   require `syke doctor --json` to be fully green
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "linux-managed-service-smoke must run on Linux" >&2
  exit 2
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl is required" >&2
  exit 2
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "systemd --user is not available for this shell/session" >&2
  echo "Start a user systemd session, then retry. For boot persistence, also check loginctl linger." >&2
  exit 2
fi

if ! command -v "$SYKE_BIN" >/dev/null 2>&1; then
  echo "syke executable not found: $SYKE_BIN" >&2
  exit 2
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$(pwd)/.syke-linux-service-smoke/$(date +%Y%m%d-%H%M%S)"
fi
mkdir -p "$OUTPUT_DIR"

cleanup() {
  if [[ "$STOP_AFTER" == "1" ]]; then
    "$SYKE_BIN" --user "$USER_ID" daemon stop >>"$OUTPUT_DIR/daemon-stop.log" 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[linux-service-smoke] syke: $("$SYKE_BIN" --version)"
echo "[linux-service-smoke] user: $USER_ID"
echo "[linux-service-smoke] output: $OUTPUT_DIR"

"$SYKE_BIN" --user "$USER_ID" daemon stop >"$OUTPUT_DIR/daemon-stop-before.log" 2>&1 || true

echo "[linux-service-smoke] starting managed daemon"
"$SYKE_BIN" --user "$USER_ID" daemon start --interval "$INTERVAL" \
  >"$OUTPUT_DIR/daemon-start.log" 2>&1

"$SYKE_BIN" --user "$USER_ID" daemon status --json >"$OUTPUT_DIR/daemon-status.json"
"$SYKE_BIN" --user "$USER_ID" status --json >"$OUTPUT_DIR/status.json"

python3 - "$OUTPUT_DIR/daemon-status.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
service = payload.get("service") or {}
persistence = payload.get("persistence") or {}
warm_runtime = payload.get("warm_runtime") or {}

assert payload.get("running") is True, payload
assert payload.get("state") == "running", payload
assert service.get("manager") == "systemd", service
assert service.get("registered") is True, service
assert service.get("scheduled_only") is False, service
assert persistence.get("manager") == "systemd", persistence
assert persistence.get("keeps_daemon_alive") is True, persistence
assert persistence.get("serves_timeline_while_idle") is True, persistence
assert isinstance(warm_runtime, dict), payload
PY

python3 - "$OUTPUT_DIR" "$WEB_PORT" <<'PY'
import json
import sys
import time
import urllib.request
from pathlib import Path

out = Path(sys.argv[1])
base = f"http://127.0.0.1:{sys.argv[2]}"
last = None

for _ in range(60):
    try:
        with urllib.request.urlopen(f"{base}/api/health", timeout=5) as response:
            health = json.load(response)
        with urllib.request.urlopen(f"{base}/api/timeline?days=30", timeout=5) as response:
            timeline = json.load(response)
        (out / "web-health.json").write_text(json.dumps(health, indent=2), encoding="utf-8")
        (out / "web-timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
        assert health.get("db_present") is True, health
        onboarding = health.get("onboarding")
        if isinstance(onboarding, dict):
            health_persistence = onboarding.get("persistence") or {}
            if health_persistence:
                assert health_persistence.get("manager") == "systemd", onboarding
                assert health_persistence.get("keeps_daemon_alive") is True, onboarding
                assert health_persistence.get("serves_timeline_while_idle") is True, onboarding
        assert "events" in timeline, timeline
        assert "count" in timeline or "cycles" in timeline, timeline
        break
    except Exception as exc:
        last = exc
        time.sleep(1)
else:
    raise SystemExit(f"timeline API did not become ready: {last}")
PY

set +e
"$SYKE_BIN" --user "$USER_ID" doctor --json >"$OUTPUT_DIR/doctor.json"
doctor_exit=$?
set -e

if [[ "$REQUIRE_DOCTOR_OK" == "1" && "$doctor_exit" -ne 0 ]]; then
  echo "doctor failed; see $OUTPUT_DIR/doctor.json" >&2
  exit "$doctor_exit"
fi

python3 - "$OUTPUT_DIR/doctor.json" "$REQUIRE_DOCTOR_OK" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
require_ok = sys.argv[2] == "1"
checks = payload.get("checks") or {}
daemon = checks.get("daemon") or {}
ipc = checks.get("daemon_ipc") or {}

assert daemon.get("ok") is True, payload
assert daemon.get("manager") == "systemd", daemon
assert (daemon.get("service") or {}).get("scheduled_only") is False, daemon
assert ipc.get("ok") is True, payload
if require_ok:
    assert payload.get("ok") is True, payload
PY

echo "[linux-service-smoke] passed; artifacts in $OUTPUT_DIR"
