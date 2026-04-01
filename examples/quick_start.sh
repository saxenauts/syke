#!/usr/bin/env bash
set -euo pipefail

USER_ID="${1:?Usage: ./quick_start.sh <user-id>}"

echo "==> Setting up Syke for user: $USER_ID"

syke --user "$USER_ID" setup --yes

syke --user "$USER_ID" auth status
syke --user "$USER_ID" doctor
syke --user "$USER_ID" status

echo "==> Done. Database: ~/.syke/data/$USER_ID/syke.db"
echo "==> Check the memex with: syke --user $USER_ID context"
echo "==> Routed workspace artifact appears at: ~/.syke/workspace/MEMEX.md"
