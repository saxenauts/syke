#!/usr/bin/env bash
# Fresh install smoke test for the current checkout.
#
# Usage:
#   bash scripts/fresh-install-test.sh          # dry run
#   bash scripts/fresh-install-test.sh --run    # execute

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

DRY_RUN=true
[[ "${1:-}" == "--run" ]] && DRY_RUN=false

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

step() { echo -e "\n${BOLD}${CYAN}[$1]${RESET} $2"; }
ok()   { echo -e "  ${GREEN}OK${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}WARN${RESET}  $1"; }
dry()  { echo -e "  ${DIM}(dry run) would: $1${RESET}"; }

run_or_dry() {
    if $DRY_RUN; then
        dry "$*"
    else
        eval "$@"
    fi
}

echo -e "${BOLD}Syke Fresh Install Smoke${RESET}"
echo -e "${DIM}$(date)${RESET}"
if $DRY_RUN; then
    echo -e "${YELLOW}DRY RUN — pass --run to execute${RESET}"
fi

step "1" "Pre-flight"
echo -e "  Python: $(python3 --version 2>&1)"
echo -e "  uv:     $(uv --version 2>&1 || echo 'not installed')"
echo -e "  Repo:   $REPO_DIR"

step "2" "Install current checkout into the managed tool environment"
run_or_dry "cd \"$REPO_DIR\" && uv tool install --force --reinstall --refresh --no-cache ."
ok "Managed install command ready"

step "3" "Core CLI smoke"
run_or_dry "syke --help"
run_or_dry "syke setup --help"
run_or_dry "syke ask --help"
run_or_dry "syke auth status --json"
run_or_dry "syke doctor --json"
ok "Core command smoke prepared"

step "4" "Installed binary path"
run_or_dry "which syke"
run_or_dry "syke --version"
ok "Binary resolution prepared"

echo ""
echo -e "${BOLD}Next manual checks${RESET}"
echo -e "  1. Run ${CYAN}syke auth status${RESET}"
echo -e "  2. Run ${CYAN}syke doctor${RESET}"
echo -e "  3. Run ${CYAN}syke setup${RESET} if you want a real onboarding pass"
echo -e "  4. Run ${CYAN}syke ask ...${RESET} after provider setup"
