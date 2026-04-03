#!/usr/bin/env bash

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
Usage: bash scripts/dev-reset.sh [--yes] [--keep-tool]

Complete local Syke reset for macOS-focused dev testing:
- stop and unload the launchd daemon if present
- remove the launch agent plist
- remove ~/.syke
- remove ~/.config/syke
- best-effort uninstall syke from uv tool and pipx

Options:
  --yes        Skip confirmation prompt
  --keep-tool  Keep uv/pipx-installed syke binaries; only remove state + daemon files
EOF
  exit 0
fi

ASSUME_YES=false
KEEP_TOOL=false

for arg in "$@"; do
  case "$arg" in
    --yes)
      ASSUME_YES=true
      ;;
    --keep-tool)
      KEEP_TOOL=true
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

LAUNCH_AGENT_PATH="$HOME/Library/LaunchAgents/com.syke.daemon.plist"
CONFIG_DIR="$HOME/.config/syke"
SYKE_HOME_DIR="$HOME/.syke"
PID_FILE="$CONFIG_DIR/daemon.pid"

echo "Syke Dev Reset"
echo "  launch agent: $LAUNCH_AGENT_PATH"
echo "  config dir:   $CONFIG_DIR"
echo "  syke home:    $SYKE_HOME_DIR"
if ! $KEEP_TOOL; then
  echo "  tool uninstall: uv tool / pipx (best effort)"
fi

if ! $ASSUME_YES; then
  printf "Continue with full local reset? [y/N]: "
  read -r reply
  case "$reply" in
    y|Y|yes|YES)
      ;;
    *)
      echo "aborted"
      exit 1
      ;;
  esac
fi

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping daemon pid $pid"
    kill "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      echo "Force-killing daemon pid $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
fi

if [[ -f "$LAUNCH_AGENT_PATH" ]]; then
  echo "Booting out launch agent"
  launchctl bootout "gui/$(id -u)" "$LAUNCH_AGENT_PATH" 2>/dev/null || true
  rm -f "$LAUNCH_AGENT_PATH"
fi

echo "Removing $CONFIG_DIR"
rm -rf "$CONFIG_DIR"

echo "Removing $SYKE_HOME_DIR"
rm -rf "$SYKE_HOME_DIR"

if ! $KEEP_TOOL; then
  if command -v uv >/dev/null 2>&1; then
    echo "Attempting uv tool uninstall syke"
    uv tool uninstall syke >/dev/null 2>&1 || true
  fi

  if command -v pipx >/dev/null 2>&1; then
    echo "Attempting pipx uninstall syke"
    pipx uninstall syke >/dev/null 2>&1 || true
  fi
fi

echo "Reset complete"
echo "Next:"
echo "  - from this repo: uv run syke setup"
echo "  - global reinstall: uv tool install syke  or  pipx install syke"
