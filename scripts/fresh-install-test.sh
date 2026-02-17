#!/usr/bin/env bash
# fresh-install-test.sh — Clean wipe + fresh install test for Syke v0.2.7
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

echo -e "${BOLD}Syke v0.2.7 Fresh Install Test${RESET}"
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

step "1b" "Remove Syke from Claude Code MCP config (~/.claude.json)"
if [ -f ~/.claude.json ]; then
    if grep -q '"syke"' ~/.claude.json 2>/dev/null; then
        if $DRY_RUN; then
            dry "Remove 'syke' entry from ~/.claude.json mcpServers"
        else
            # Use python to safely remove the syke key from mcpServers
            python3 -c "
import json, sys
p = '$HOME/.claude.json'
with open(p) as f: d = json.load(f)
if 'mcpServers' in d and 'syke' in d['mcpServers']:
    del d['mcpServers']['syke']
    with open(p, 'w') as f: json.dump(d, f, indent=2)
    print('  removed syke from mcpServers')
else:
    print('  syke not in mcpServers')
"
        fi
        ok "Cleaned ~/.claude.json"
    else
        ok "No syke entry in ~/.claude.json"
    fi
else
    ok "~/.claude.json doesn't exist"
fi

step "1c" "Remove Syke from Claude Desktop MCP config"
DESKTOP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [ -f "$DESKTOP_CONFIG" ]; then
    if grep -q '"syke"' "$DESKTOP_CONFIG" 2>/dev/null; then
        if $DRY_RUN; then
            dry "Remove 'syke' entry from Claude Desktop config"
        else
            python3 -c "
import json
p = '$DESKTOP_CONFIG'
with open(p) as f: d = json.load(f)
if 'mcpServers' in d and 'syke' in d['mcpServers']:
    del d['mcpServers']['syke']
    with open(p, 'w') as f: json.dump(d, f, indent=2)
"
        fi
        ok "Cleaned Claude Desktop config"
    else
        ok "No syke entry in Claude Desktop config"
    fi
else
    ok "Claude Desktop config doesn't exist"
fi

step "1d" "Remove Syke from project .mcp.json (if exists)"
PROJECT_MCP="$(pwd)/.mcp.json"
if [ -f "$PROJECT_MCP" ]; then
    if grep -q '"syke"' "$PROJECT_MCP" 2>/dev/null; then
        if $DRY_RUN; then
            dry "Remove 'syke' entry from $PROJECT_MCP"
        else
            python3 -c "
import json
p = '$PROJECT_MCP'
with open(p) as f: d = json.load(f)
if 'mcpServers' in d and 'syke' in d['mcpServers']:
    del d['mcpServers']['syke']
    with open(p, 'w') as f: json.dump(d, f, indent=2)
"
        fi
        ok "Cleaned $PROJECT_MCP"
    else
        ok "No syke entry in $PROJECT_MCP"
    fi
else
    ok "No .mcp.json in project"
fi

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

step "2c" "Check API key"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    ok "ANTHROPIC_API_KEY is set (${#ANTHROPIC_API_KEY} chars)"
else
    warn "ANTHROPIC_API_KEY not set — perception will be skipped"
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

step "4b" "Check MCP config injected"
if [ -f ~/.claude.json ] && grep -q '"syke"' ~/.claude.json 2>/dev/null; then
    ok "syke entry in ~/.claude.json"
else
    fail "syke NOT in ~/.claude.json"
fi

step "4c" "Check Claude Desktop config"
if [ -f "$DESKTOP_CONFIG" ] && grep -q '"syke"' "$DESKTOP_CONFIG" 2>/dev/null; then
    ok "syke entry in Claude Desktop config"
else
    warn "syke not in Claude Desktop config (may not be applicable)"
fi

step "4d" "Check data directory created"
if [ -d ~/.syke ]; then
    DB_COUNT=$(find ~/.syke -name "*.db" | wc -l | tr -d ' ')
    ok "~/.syke exists ($DB_COUNT database files)"
else
    fail "~/.syke not created"
fi

step "4e" "Check profile exists"
PROFILE=$(find ~/.syke -name "profile.json" 2>/dev/null | head -1)
if [ -n "$PROFILE" ] && [ -f "$PROFILE" ]; then
    ok "Profile found: $PROFILE"
    if $DRY_RUN; then
        dry "would show identity anchor"
    else
        python3 -c "
import json
with open('$PROFILE') as f: p = json.load(f)
print(f\"  Identity: {p.get('identity_anchor', 'N/A')[:100]}...\")
print(f\"  Threads:  {len(p.get('active_threads', []))}\")
print(f\"  Sources:  {', '.join(p.get('sources', []))}\")
print(f\"  Events:   {p.get('events_count', 0)}\")
"
    fi
else
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        warn "No profile.json — perception may have failed"
    else
        ok "No profile (expected — no API key)"
    fi
fi

# ─── Phase 5: Summary ────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}Test complete.${RESET}"
echo ""
echo -e "Next steps:"
echo -e "  1. ${CYAN}Restart Claude Code${RESET} to activate MCP server"
echo -e "  2. Start a new session and test: ask Syke about yourself"
echo -e "  3. Check MCP tools: get_profile, query_timeline, search_events"
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo -e "  4. ${YELLOW}To build profile:${RESET} export ANTHROPIC_API_KEY=sk-ant-... && syke sync --rebuild"
fi
echo ""
