#!/usr/bin/env bash
# GPT-5.4-mini baseline experiments — 3 rounds × 4 conditions × 31 days
# Same protocol as Sonnet baselines (March 22), now on azure/gpt-5.4-mini via LiteLLM proxy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="$REPO_DIR/.venv/bin/python"
RUNNER="$SCRIPT_DIR/memory_replay.py"
SOURCE_DB="$SCRIPT_DIR/data/frozen_saxenauts.db"
RUNS_DIR="$SCRIPT_DIR/runs"
PROMPTS_DIR="$SCRIPT_DIR/prompts"

CONDITIONS=(zero minimal minimal_exclude single_doc)
ROUNDS=(1 2 3)
MAX_DAYS=31
START_DAY="2026-01-17"

TOTAL_RUNS=$(( ${#ROUNDS[@]} * ${#CONDITIONS[@]} ))
RUN_NUM=0
TOTAL_COST=0

echo "=============================================="
echo "GPT-5.4-mini Baselines — $TOTAL_RUNS runs"
echo "=============================================="
echo "Source DB: $SOURCE_DB"
echo "Start day: $START_DAY | Max days: $MAX_DAYS"
echo "Conditions: ${CONDITIONS[*]}"
echo "Rounds: ${ROUNDS[*]}"
echo "Started: $(date)"
echo "=============================================="
echo ""

for ROUND in "${ROUNDS[@]}"; do
    echo "====== ROUND $ROUND ======"
    for COND in "${CONDITIONS[@]}"; do
        RUN_NUM=$((RUN_NUM + 1))
        RUN_NAME="gpt54m_r${ROUND}_${COND}"
        OUTPUT_DIR="$RUNS_DIR/$RUN_NAME"
        LOG_FILE="$RUNS_DIR/${RUN_NAME}.log"
        SKILL_FILE="$PROMPTS_DIR/${COND}.md"

        echo ""
        echo "--- Run $RUN_NUM/$TOTAL_RUNS: $RUN_NAME ---"
        echo "  Skill: $SKILL_FILE"
        echo "  Output: $OUTPUT_DIR"
        echo "  Log: $LOG_FILE"
        echo "  Start: $(date)"

        $PYTHON "$RUNNER" \
            --source-db "$SOURCE_DB" \
            --output-dir "$OUTPUT_DIR" \
            --source-user-id fresh_test \
            --max-days "$MAX_DAYS" \
            --start-day "$START_DAY" \
            --skill "$SKILL_FILE" \
            2>&1 | tee "$LOG_FILE"

        EXIT_CODE=${PIPESTATUS[0]}
        if [ $EXIT_CODE -ne 0 ]; then
            echo "  FAILED (exit code $EXIT_CODE)"
            echo "  Check log: $LOG_FILE"
            echo "  Continuing to next run..."
            continue
        fi

        # Extract cost from last line of output
        COST=$(grep -o '\$[0-9.]*' "$LOG_FILE" | tail -1 | tr -d '$' || echo "0")
        TOTAL_COST=$(echo "$TOTAL_COST + $COST" | bc 2>/dev/null || echo "$TOTAL_COST")
        echo "  Done: $(date) | Cost: \$$COST | Cumulative: \$$TOTAL_COST"
    done
    echo ""
    echo "====== ROUND $ROUND COMPLETE ======"
    echo ""
done

echo "=============================================="
echo "ALL RUNS COMPLETE"
echo "Total runs: $RUN_NUM/$TOTAL_RUNS"
echo "Total cost: \$$TOTAL_COST"
echo "Finished: $(date)"
echo "=============================================="
