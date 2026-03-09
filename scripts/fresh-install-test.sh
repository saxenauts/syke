#!/usr/bin/env bash
# Simulates what a brand-new agent would encounter.
#
# Usage:
#   bash scripts/fresh-install-test.sh          # dry run (shows what it would do)
#   bash scripts/fresh-install-test.sh --run     # actually execute

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

step() { echo -e "\n${BOLD}${CYAN}[$1]${RESET} $2"; }
ok()   { echo -e "  ${GREEN}OK${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}WARN${RESET}  $1"; }
fail() { echo -e "  ${RED}FAIL${RESET}  $1"; }
dry()  { echo -e "  ${DIM}(dry run) would: $1${RESET}"; }

run_or_dry() {
    if $DRY_RUN; then
        dry "$*"
    else
        eval "$@"
    fi
}

echo -e "${BOLD}Syke v0.4.5 Fresh Install Test${RESET}"
echo -e "${DIM}$(date)${RESET}"
if $DRY_RUN; then
    echo -e "${YELLOW}DRY RUN — pass --run to execute${RESET}"
fi

# ─── Phase 1: Clean up existing Syke ─────────────────────────────────────────

step "1a" "Remove Syke data directory"
if [ -d ~/.syke ]; then
    run_or_dry "rm -rf ~/.syke"
    ok "Removed ~/.syke"
else
    ok "~/.syke doesn't exist (already clean)"
fi

step "1b" "Remove Syke include from ~/.claude/CLAUDE.md"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [ -f "$CLAUDE_MD" ] && grep -q '.syke/data/.*/CLAUDE.md' "$CLAUDE_MD" 2>/dev/null; then
    if $DRY_RUN; then
        dry "Remove Syke @include from $CLAUDE_MD"
    else
        python3 -c "
from pathlib import Path
p = Path('$CLAUDE_MD')
lines = p.read_text().splitlines()
lines = [line for line in lines if '.syke/data/' not in line or 'CLAUDE.md' not in line]
p.write_text(('\n'.join(lines).rstrip() + '\n') if lines else '')
print('  removed Syke include')
"
    fi
    ok "Cleaned $CLAUDE_MD"
else
    ok "No Syke include in ~/.claude/CLAUDE.md"
fi

step "1c" "Remove Syke data dir from Claude Desktop trusted folders"
DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [ -f "$DESKTOP_CONFIG" ]; then
    if grep -q 'localAgentModeTrustedFolders' "$DESKTOP_CONFIG" 2>/dev/null; then
        if $DRY_RUN; then
            dry "Remove ~/.syke/data from Claude Desktop trusted folders"
        else
            python3 -c "
import json
p = '$DESKTOP_CONFIG'
with open(p) as f: d = json.load(f)
prefs = d.setdefault('preferences', {})
trusted = prefs.setdefault('localAgentModeTrustedFolders', [])
trusted = [item for item in trusted if item != '$HOME/.syke/data']
prefs['localAgentModeTrustedFolders'] = trusted
with open(p, 'w') as f: json.dump(d, f, indent=2)
print('  cleaned trusted folders')
"
        fi
        ok "Cleaned Claude Desktop config"
    else
        ok "No syke entry in Claude Desktop config"
    fi
else
    ok "Claude Desktop config doesn't exist"
fi

step "1d" "Remove installed Syke skills from agent directories"
for skills_dir in "$HOME/.claude/skills/syke" "$HOME/.codex/skills/syke" "$HOME/.cursor/skills/syke" "$HOME/.windsurf/skills/syke" "$HOME/.hermes/skills/memory/syke"; do
    if [ -d "$skills_dir" ]; then
        run_or_dry "rm -rf '$skills_dir'"
        ok "Removed $skills_dir"
    fi
done

step "1e" "Remove Syke hooks from Claude Code settings"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [ -f "$CLAUDE_SETTINGS" ]; then
    if grep -q 'syke' "$CLAUDE_SETTINGS" 2>/dev/null; then
        if $DRY_RUN; then
            dry "Remove syke hooks from $CLAUDE_SETTINGS"
        else
            python3 -c "
import json
p = '$CLAUDE_SETTINGS'
with open(p) as f: d = json.load(f)
changed = False
for hook_type in ['hooks', 'projectHooks']:
    if hook_type in d:
        for event_name in list(d[hook_type].keys()):
            if isinstance(d[hook_type][event_name], list):
                orig = len(d[hook_type][event_name])
                d[hook_type][event_name] = [
                    h for h in d[hook_type][event_name]
                    if 'syke' not in json.dumps(h).lower()
                ]
                if len(d[hook_type][event_name]) < orig:
                    changed = True
if changed:
    with open(p, 'w') as f: json.dump(d, f, indent=2)
    print('  removed syke hooks')
else:
    print('  no syke hooks found')
"
        fi
        ok "Cleaned hooks"
    else
        ok "No syke hooks in settings"
    fi
else
    ok "No Claude settings file"
fi

step "1f" "Uninstall Syke from pipx/uvx cache"
run_or_dry "pipx uninstall syke 2>/dev/null || true"
run_or_dry "uv cache clean syke 2>/dev/null || true"
ok "Package caches cleared"

step "1g" "Stop Syke daemon if running"
if launchctl list 2>/dev/null | grep -q syke; then
    run_or_dry "launchctl bootout gui/\$(id -u) ~/Library/LaunchAgents/com.syke.daemon.plist 2>/dev/null || true"
    ok "Daemon stopped"
else
    ok "No daemon running"
fi
if [ -f ~/Library/LaunchAgents/com.syke.daemon.plist ]; then
    run_or_dry "rm -f ~/Library/LaunchAgents/com.syke.daemon.plist"
    ok "Removed LaunchAgent plist"
fi

# ─── Phase 2: Pre-flight checks ──────────────────────────────────────────────

step "2a" "Check prerequisites"
echo -e "  Python:  $(python3 --version 2>&1)"
echo -e "  uv:     $(uv --version 2>&1 || echo 'not installed')"
echo -e "  gh:     $(gh --version 2>&1 | head -1 || echo 'not installed')"

step "2b" "Check gh auth"
if gh auth status &>/dev/null; then
    GH_USER=$(gh api user --jq '.login' 2>/dev/null || echo "unknown")
    ok "gh authenticated as @$GH_USER"
else
    warn "gh not authenticated — GitHub ingestion will use unauthenticated API (60 req/hr)"
fi

step "2c" "Check Claude auth"
if command -v claude &>/dev/null && [ -d ~/.claude/ ]; then
    ok "Claude authenticated (~/.claude/ exists)"
else
    warn "Claude not authenticated — claude-login provider won't be available"
fi

# ─── Phase 3: Fresh install ──────────────────────────────────────────────────

step "3" "Install and run: uvx syke setup --yes"
if $DRY_RUN; then
    dry "uvx syke setup --yes"
else
    echo ""
    uvx syke setup --yes
    echo ""
fi

# ─── Phase 4: Verify ─────────────────────────────────────────────────────────

step "4a" "Check Syke status"
if $DRY_RUN; then
    dry "uvx syke status"
else
    uvx syke status
fi

step "4b" "Check Claude Code include"
if [ -f "$CLAUDE_MD" ] && grep -q '.syke/data/.*/CLAUDE.md' "$CLAUDE_MD" 2>/dev/null; then
    ok "Syke include present in ~/.claude/CLAUDE.md"
else
    warn "Syke include not present in ~/.claude/CLAUDE.md"
fi

step "4c" "Check Claude Desktop config"
if [ -f "$DESKTOP_CONFIG" ] && grep -q 'localAgentModeTrustedFolders' "$DESKTOP_CONFIG" 2>/dev/null; then
    ok "Claude Desktop config updated"
else
    warn "Claude Desktop config not updated (may not be applicable)"
fi

step "4d" "Check data directory created"
if [ -d ~/.syke ]; then
    DB_COUNT=$(find ~/.syke -name "*.db" | wc -l | tr -d ' ')
    ok "~/.syke exists ($DB_COUNT database files)"
else
    fail "~/.syke not created"
fi

step "4e" "Check memex/distribution files"
MEMEX=$(find ~/.syke -name "CLAUDE.md" 2>/dev/null | head -1)
if [ -n "$MEMEX" ] && [ -f "$MEMEX" ]; then
    ok "Memex found: $MEMEX"
else
    warn "No memex yet — synthesis may still be waiting for enough events or the next daemon tick"
fi

# ─── Phase 5: Summary ────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}Test complete.${RESET}"
echo ""
echo -e "Next steps:"
echo -e "  1. ${CYAN}Restart Claude Code${RESET} to pick up the latest include/skills"
echo -e "  2. Start a new session and test: ask Syke about yourself"
echo -e "  3. Run ${CYAN}syke doctor${RESET} and ${CYAN}syke status${RESET} to confirm auth + daemon state"
echo ""
echo ""
