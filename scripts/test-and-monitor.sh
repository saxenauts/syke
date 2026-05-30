#!/usr/bin/env bash
# Syke test & monitor script
# Usage: bash scripts/test-and-monitor.sh [test|monitor|status|all]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$REPO_DIR/.venv/bin"
SYKE="$VENV/syke"
DATA_DIR="$HOME/.syke"
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
    require_repo_venv

    header "Syke Status"
    "$SYKE" status 2>&1

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

    header "Recent Cycles"
    "$VENV/python" - <<'PY'
from syke.config import DEFAULT_USER, user_syke_db_path
from syke.db import SykeDB

user_id = DEFAULT_USER
db_path = user_syke_db_path(user_id)
if not db_path.exists():
    print("  No syke.db yet")
    raise SystemExit(0)

db = SykeDB(str(db_path))
rows = db.conn.execute(
    """SELECT status, completed_at, started_at, duration_ms, memex_updated
       FROM cycle_records
       WHERE user_id = ?
       ORDER BY COALESCE(completed_at, started_at) DESC, id DESC
       LIMIT 5""",
    (user_id,),
).fetchall()

if not rows:
    print("  No cycles yet")

for row in rows:
    status = row["status"]
    icon = "✓" if status == "completed" else "✗" if status == "failed" else "·"
    duration = f"{int(row['duration_ms'] or 0)}ms"
    memex = "memex" if row["memex_updated"] else "no-memex"
    timestamp = row["completed_at"] or row["started_at"] or "unknown"
    print(f"  {icon} {status:10s} {duration:>8s} {memex:>8s}  {timestamp}")
PY

    header "Database"
    "$VENV/python" -c "
from syke.db import SykeDB
from syke.config import DEFAULT_USER, user_syke_db_path
user_id = DEFAULT_USER
db = SykeDB(user_syke_db_path(user_id))
mem_count = db.count_memories(user_id, active_only=True)
cycle_count = db.conn.execute('SELECT COUNT(*) FROM cycle_records WHERE user_id = ?', (user_id,)).fetchone()[0]
print(f'  Memories: {mem_count} active')
print(f'  Cycles:   {cycle_count}')
"
}

cmd_monitor() {
    header "Live Monitor (Ctrl+C to stop)"
    echo "Watching: daemon.log + syke.log"
    echo

    tail -f \
        "$DAEMON_LOG" \
        "$DATA_DIR/syke.log" 2>/dev/null
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
