#!/usr/bin/env bash
set -euo pipefail

echo "==> Setting up Syke"

syke setup --yes

syke auth status
syke doctor
syke status

echo "==> Done. Database: ~/.syke/syke.db"
echo "==> Check the memex with: syke memex"
echo "==> Memex artifact lives at: ~/.syke/MEMEX.md"
