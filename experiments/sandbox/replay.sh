#!/usr/bin/env bash
# Convenience wrapper: run a replay experiment in the sandbox container.
#
# Usage:
#   ./replay.sh                                    # zero prompt, 7 days from start
#   ./replay.sh --skill ../prompts/minimal.md      # with prompt
#   ./replay.sh --days 31 --start 2026-01-15       # golden gate window
#   ./replay.sh --name my_experiment                # custom run name
#
# Environment:
#   AZURE_API_KEY, AZURE_API_BASE — required for Azure provider
#   ANTHROPIC_API_KEY — required for direct Anthropic
#   FROZEN_DB — path to frozen dataset (default: ../data/frozen_saxenauts.db)

set -euo pipefail
cd "$(dirname "$0")"

# Parse args
NAME="sandbox_run_$(date +%s)"
SKILL=""
DAYS="7"
START=""
SOURCE_USER="fresh_test"
USER_ID="replay_sandbox"

while [[ $# -gt 0 ]]; do
    case $1 in
        --skill) SKILL="$(realpath "$2")"; shift 2 ;;
        --days) DAYS="$2"; shift 2 ;;
        --start) START="$2"; shift 2 ;;
        --name) NAME="$2"; shift 2 ;;
        --user) USER_ID="$2"; shift 2 ;;
        --source-user) SOURCE_USER="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

FROZEN_DB="${FROZEN_DB:-$(realpath ../data/frozen_saxenauts.db)}"
OUTPUT_DIR="$(realpath ../runs)/$NAME"
mkdir -p "$OUTPUT_DIR"

echo "=== Sandbox Replay ==="
echo "  Name:   $NAME"
echo "  DB:     $FROZEN_DB"
echo "  Output: $OUTPUT_DIR"
echo "  Skill:  ${SKILL:-'(zero prompt)'}"
echo "  Window: start=${START:-beginning}, days=$DAYS"
echo ""

# Build (if needed) and run
SKILL_MOUNT="${SKILL:-/dev/null}"

FROZEN_DB="$FROZEN_DB" \
OUTPUT_DIR="$OUTPUT_DIR" \
SKILL_FILE="$SKILL_MOUNT" \
REPLAY_USER_ID="$USER_ID" \
REPLAY_SOURCE_USER_ID="$SOURCE_USER" \
REPLAY_START_DAY="$START" \
REPLAY_MAX_DAYS="$DAYS" \
docker compose up --build --abort-on-container-exit

echo ""
echo "Results: $OUTPUT_DIR/replay_results.json"
echo "View:    open http://127.0.0.1:8433/replay_viz.html"
