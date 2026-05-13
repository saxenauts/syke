#!/usr/bin/env bash
# Agent-friendly Syke bootstrap for a fresh machine.
#
# This script is intentionally non-interactive by default for agent use.
# It installs Syke from the current checkout, runs `setup --agent`, and
# optionally completes provider auth + first sync when env vars are set.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

SYKE_BIN="${SYKE_BIN:-syke}"
SYKE_SKIP_DAEMON="${SYKE_SKIP_DAEMON:-0}"
SYKE_BOOTSTRAP_SYNC="${SYKE_BOOTSTRAP_SYNC:-1}"
SYKE_REUSE_EXISTING="${SYKE_REUSE_EXISTING:-0}"

SYKE_PROVIDER="${SYKE_PROVIDER:-}"
SYKE_API_KEY="${SYKE_API_KEY:-}"
SYKE_MODEL="${SYKE_MODEL:-}"
SYKE_BASE_URL="${SYKE_BASE_URL:-}"
SYKE_USER_VALUE="${SYKE_USER:-}"

log() {
  printf '[syke-install] %s\n' "$*"
}

die() {
  printf '[syke-install] ERROR: %s\n' "$*" >&2
  exit 1
}

json_get() {
  local json_file="$1"
  local key="$2"
  python3 - "$json_file" "$key" <<'PY'
import json
import sys

path = sys.argv[1]
key = sys.argv[2]
with open(path, encoding="utf-8") as fh:
    payload = json.load(fh)
value = payload
for part in key.split("."):
    if isinstance(value, dict):
        value = value.get(part)
    else:
        value = None
        break
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(str(value))
PY
}

ensure_syke_installed() {
  if [[ "$SYKE_REUSE_EXISTING" == "1" ]] && command -v "$SYKE_BIN" >/dev/null 2>&1; then
    SYKE_BIN="$(command -v "$SYKE_BIN")"
    log "using existing '$SYKE_BIN' on PATH (SYKE_REUSE_EXISTING=1)"
    return
  fi

  if command -v uv >/dev/null 2>&1; then
    log "installing Syke from current checkout via uv tool install"
    uv tool install --force --directory "$REPO_DIR" . >/dev/null
    local uv_bin_dir
    uv_bin_dir="$(uv tool dir --bin)"
    if [[ -x "$uv_bin_dir/syke" ]]; then
      SYKE_BIN="$uv_bin_dir/syke"
      return
    fi
    if command -v syke >/dev/null 2>&1; then
      SYKE_BIN="$(command -v syke)"
      return
    fi
    die "uv installed Syke but no syke executable was found in '$uv_bin_dir'"
  fi

  if command -v pipx >/dev/null 2>&1; then
    log "installing Syke from current checkout via pipx"
    pipx install --force "$REPO_DIR" >/dev/null
    if command -v syke >/dev/null 2>&1; then
      SYKE_BIN="$(command -v syke)"
      return
    fi
    if [[ -x "$HOME/.local/bin/syke" ]]; then
      SYKE_BIN="$HOME/.local/bin/syke"
      return
    fi
    die "pipx installed Syke but no syke executable was found on PATH or in ~/.local/bin"
  fi

  if command -v "$SYKE_BIN" >/dev/null 2>&1; then
    SYKE_BIN="$(command -v "$SYKE_BIN")"
    log "uv/pipx unavailable; falling back to existing '$SYKE_BIN'"
    return
  fi

  die "missing installer: install uv or pipx, or install syke manually first"
}

build_user_args() {
  if [[ -n "$SYKE_USER_VALUE" ]]; then
    printf -- "--user\n%s\n" "$SYKE_USER_VALUE"
  fi
}

run_setup_agent() {
  local out_json="$1"
  local -a cmd
  cmd=("$SYKE_BIN")
  while IFS= read -r line; do
    cmd+=("$line")
  done < <(build_user_args)
  cmd+=("setup" "--agent")
  if [[ "$SYKE_SKIP_DAEMON" == "1" ]]; then
    cmd+=("--skip-daemon")
  fi

  set +e
  "${cmd[@]}" >"$out_json"
  local code=$?
  set -e
  printf '%s' "$code"
}

configure_provider_from_env() {
  [[ -n "$SYKE_PROVIDER" ]] || return 1
  [[ -n "$SYKE_API_KEY" ]] || return 1

  local -a cmd
  cmd=("$SYKE_BIN")
  while IFS= read -r line; do
    cmd+=("$line")
  done < <(build_user_args)
  cmd+=("auth" "set" "$SYKE_PROVIDER" "--api-key" "$SYKE_API_KEY" "--use")
  if [[ -n "$SYKE_MODEL" ]]; then
    cmd+=("--model" "$SYKE_MODEL")
  fi
  if [[ -n "$SYKE_BASE_URL" ]]; then
    cmd+=("--base-url" "$SYKE_BASE_URL")
  fi

  log "configuring provider from environment: $SYKE_PROVIDER"
  "${cmd[@]}" >/dev/null
}

run_bootstrap_sync() {
  local -a cmd
  cmd=("$SYKE_BIN")
  while IFS= read -r line; do
    cmd+=("$line")
  done < <(build_user_args)
  cmd+=("sync")
  log "running first sync"
  "${cmd[@]}"
}

main() {
  ensure_syke_installed

  tmp_dir="$(mktemp -d)"
  trap "rm -rf '$tmp_dir'" EXIT

  local setup_json="$tmp_dir/setup-agent.json"

  local setup_exit
  setup_exit="$(run_setup_agent "$setup_json")"
  local status
  status="$(json_get "$setup_json" "status")"

  log "setup status: ${status:-unknown} (exit=${setup_exit})"

  if [[ "$status" == "needs_runtime" ]]; then
    log "$(json_get "$setup_json" "error")"
    die "runtime missing. Install Node.js 18+ and rerun."
  fi

  if [[ "$status" == "needs_provider" ]]; then
    if [[ -n "$SYKE_PROVIDER" || -n "$SYKE_API_KEY" ]]; then
      if [[ -z "$SYKE_PROVIDER" || -z "$SYKE_API_KEY" ]]; then
        log "SYKE_PROVIDER and SYKE_API_KEY must both be set to configure auth non-interactively."
        exit 3
      fi
    fi

    if configure_provider_from_env; then
      log "provider configured; rerunning setup --agent"
      setup_exit="$(run_setup_agent "$setup_json")"
      status="$(json_get "$setup_json" "status")"
      log "setup status after auth: ${status:-unknown} (exit=${setup_exit})"
    elif [[ -n "$SYKE_PROVIDER" && -n "$SYKE_API_KEY" ]]; then
      log "provider configuration failed for '$SYKE_PROVIDER'. Inspect the auth error above."
      exit 3
    else
      local setup_hint="syke setup --agent"
      if [[ "$SYKE_SKIP_DAEMON" == "1" ]]; then
        setup_hint="$setup_hint --skip-daemon"
      fi
      log "provider is required but SYKE_PROVIDER/SYKE_API_KEY were not set."
      log "next steps:"
      log "  syke auth set <provider> --api-key <KEY> --use"
      log "  $setup_hint"
      exit 3
    fi
  fi

  if [[ "$status" != "complete" ]]; then
    log "setup payload:"
    cat "$setup_json"
    die "setup did not complete"
  fi

  log "$(json_get "$setup_json" "instructions")"
  log "estimated minutes: $(json_get "$setup_json" "estimated_minutes")"
  log "estimate basis files: $(json_get "$setup_json" "total_files")"

  if [[ "$SYKE_SKIP_DAEMON" == "1" && "$SYKE_BOOTSTRAP_SYNC" == "1" ]]; then
    run_bootstrap_sync
  fi

  log "done"
}

main "$@"
