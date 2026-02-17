#!/usr/bin/env bash
# Syke quick start — full pipeline from zero to profile
set -euo pipefail

USER_ID="${1:?Usage: ./quick_start.sh <user-id>}"

echo "==> Setting up Syke for user: $USER_ID"

# One command does everything: detect sources, collect data, build profile, configure MCP
python -m syke --user "$USER_ID" setup --yes

# Show status
python -m syke --user "$USER_ID" status

echo "==> Done. Profile at ~/.syke/data/$USER_ID/profile.json"
