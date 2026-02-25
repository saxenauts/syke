#!/usr/bin/env bash
# Syke test & monitor script
# Usage: bash scripts/test-and-monitor.sh [test|monitor|status|all]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_ID="${SYKE_USER:-$(whoami)}"
VENV="$REPO_DIR/.venv/bin"
SYKE="$VENV/python -m syke --user $USER_ID"
DATA_DIR="$HOME/.syke/data/$USER_ID"
DAEMON_LOG="$HOME/.config/syke/daemon.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

header() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }

cmd_test() {
    header "1. Unit Tests"
    cd "$REPO_DIR"
    source "$VENV/activate"
    python -m pytest tests/ -v --tb=short 2>&1 | tail -30
    echo

    header "2. MCP Server Smoke Test"
    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' \
      | timeout 5 $SYKE serve --transport stdio 2>/dev/null | head -1 | python3 -c "
import sys, json
try:
    resp = json.loads(sys.stdin.readline())
    if 'result' in resp:
        print('${GREEN}MCP server: OK${NC} — protocol initialized')
    else:
        print('${RED}MCP server: FAIL${NC} — unexpected response')
        print(json.dumps(resp, indent=2))
except Exception as e:
    print('${RED}MCP server: FAIL${NC} —', e)
" || echo -e "${YELLOW}MCP server: timeout (expected — stdio transport exits after disconnect)${NC}"

    header "3. Config Verification"
    python3 -c "
import json, os

# Claude Code
cc = json.load(open(os.path.expanduser('~/.claude.json')))
cmd = cc.get('mcpServers', {}).get('syke', {}).get('command', 'MISSING')
print(f'Claude Code:   command={cmd}')
print(f'               absolute={os.path.isabs(cmd)}')

# Claude Desktop
desktop_path = os.path.expanduser('~/Library/Application Support/Claude/claude_desktop_config.json')
if os.path.exists(desktop_path):
    cd = json.load(open(desktop_path))
    cmd2 = cd.get('mcpServers', {}).get('syke', {}).get('command', 'MISSING')
    print(f'Claude Desktop: command={cmd2}')
    print(f'               absolute={os.path.isabs(cmd2)}')
else:
    print('Claude Desktop: not configured')
"

    header "4. Manual Sync Test"
    $SYKE sync --force 2>&1
}

cmd_status() {
    header "Syke Status"
    $SYKE status 2>&1

    header "Daemon"
    if launchctl list 2>/dev/null | grep -q com.syke.daemon; then
        echo -e "${GREEN}Daemon: running${NC} (com.syke.daemon)"
        PID=$(launchctl list | grep com.syke.daemon | awk '{print $1}')
        echo "  PID: $PID"
    else
        echo -e "${RED}Daemon: not running${NC}"
    fi
    echo "  Interval: every 15 min"
    echo "  Log: $DAEMON_LOG"

    header "Recent Metrics"
    if [ -f "$DATA_DIR/metrics.jsonl" ]; then
        tail -5 "$DATA_DIR/metrics.jsonl" | python3 -c "
import sys, json
for line in sys.stdin:
    m = json.loads(line.strip())
    status = '✓' if m.get('success') else '✗'
    cost = f'\${m[\"cost_usd\"]:.4f}' if m.get('cost_usd', 0) > 0 else '—'
    dur = f'{m.get(\"duration_seconds\", 0):.1f}s'
    print(f'  {status} {m[\"operation\"]:30s} {dur:>8s}  {cost:>8s}  events={m.get(\"events_processed\", 0)}')
"
    fi

    header "Database"
    python3 -c "
from syke.db import SykeDB
from pathlib import Path
import os
user_id = os.environ.get('SYKE_USER') or os.popen('whoami').read().strip()
db = SykeDB(str(Path.home() / f'.syke/data/{user_id}/syke.db'))
s = db.get_status(user_id)
print(f'  Total events: {s[\"total_events\"]}')
for src, cnt in s['sources'].items():
    print(f'    {src}: {cnt}')
profile = s.get('latest_profile', {})
if profile:
    print(f'  Profile: {profile.get(\"created_at\", \"none\")} ({profile.get(\"model\", \"?\")})')
costs = db.get_perception_cost_stats(user_id)
if costs:
    print(f'  Cost: \${costs[\"total_cost_usd\"]:.2f} total ({costs[\"run_count\"]} runs, avg \${costs[\"avg_cost_usd\"]:.2f})')
"
}

cmd_monitor() {
    header "Live Monitor (Ctrl+C to stop)"
    echo "Watching: daemon.log + syke.log + metrics.jsonl"
    echo

    tail -f \
        "$DAEMON_LOG" \
        "$DATA_DIR/syke.log" \
        "$DATA_DIR/metrics.jsonl" 2>/dev/null
}

cmd_all() {
    cmd_status
    cmd_test
    echo
    echo -e "${GREEN}All checks complete.${NC} Run 'bash scripts/test-and-monitor.sh monitor' to watch live."
}

case "${1:-all}" in
    test)    cmd_test ;;
    status)  cmd_status ;;
    monitor) cmd_monitor ;;
    all)     cmd_all ;;
    *)       echo "Usage: $0 [test|status|monitor|all]"; exit 1 ;;
esac
