#!/usr/bin/env bash
# Syke test & monitor script
# Usage: bash scripts/test-and-monitor.sh [test|monitor|status|all]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
USER_ID="${SYKE_USER:-$(whoami)}"
VENV="$REPO_DIR/.venv/bin"
SYKE="$VENV/syke --user $USER_ID"
DATA_DIR="$HOME/.syke/data/$USER_ID"
DAEMON_LOG="$HOME/.config/syke/daemon.log"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

header() { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }

require_repo_venv() {
    if [ ! -x "$VENV/python" ]; then
        echo -e "${RED}Missing repo venv at $REPO_DIR/.venv${NC}"
        echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e \".[dev]\""
        exit 1
    fi
}

cmd_test() {
    require_repo_venv
    cd "$REPO_DIR"

    header "1. Ruff"
    "$VENV/ruff" check .
    "$VENV/ruff" format --check .

    header "2. Unit Tests"
    "$VENV/python" -m pytest tests/ -v --tb=short

    header "3. Build"
    "$VENV/python" -m build
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
mem_count = db.count_memories(user_id, active_only=True)
cycle_count = db.conn.execute('SELECT COUNT(*) FROM cycle_records WHERE user_id = ?', (user_id,)).fetchone()[0]
print(f'  Memories: {mem_count} active')
print(f'  Cycles:   {cycle_count}')
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
