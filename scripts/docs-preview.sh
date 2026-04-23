#!/usr/bin/env bash
# Local preview server for iterating on Syke diagrams and HTML doc sources.
# Serves the repo root at http://127.0.0.1:8000 so both _internal/*.html and
# docs/*.svg load with working relative paths and the Mermaid CDN works.
#
# Usage:
#   bash scripts/docs-preview.sh            # foreground
#   bash scripts/docs-preview.sh --bg       # background (PID printed)
#
# Stop with Ctrl-C (foreground) or `kill <pid>` (background).
#
# Overrides:
#   SYKE_DOCS_PREVIEW_PORT  (default 8000)
#   SYKE_DOCS_PREVIEW_BIND  (default 127.0.0.1)
#
# Pairs with _internal/DIAGRAM_LOOP.md for the full iteration workflow.

set -euo pipefail

PORT="${SYKE_DOCS_PREVIEW_PORT:-8000}"
BIND="${SYKE_DOCS_PREVIEW_BIND:-127.0.0.1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

banner() {
  echo
  echo "  Syke docs preview — http://${BIND}:${PORT}"
  echo "  Serving:            ${REPO_ROOT}"
  echo
  echo "  Diagram sources:"
  shopt -s nullglob
  local any=0
  for html in _internal/syke-*.html; do
    echo "    http://${BIND}:${PORT}/${html}"
    any=1
  done
  shopt -u nullglob
  if [ "$any" -eq 0 ]; then
    echo "    (none yet — drop an HTML file in _internal/ to start)"
  fi
  echo
  echo "  Iteration loop:"
  echo "    1. edit _internal/syke-<name>.html"
  echo "    2. mcp__chrome-devtools__navigate_page to the URL above"
  echo "    3. mcp__chrome-devtools__take_screenshot"
  echo "    4. review, iterate, commit"
  echo
  echo "  Full workflow: _internal/DIAGRAM_LOOP.md"
  echo
}

case "${1:-}" in
  --bg)
    banner
    nohup python3 -m http.server "$PORT" --bind "$BIND" \
      >/tmp/syke-docs-preview.log 2>&1 &
    PID=$!
    disown "$PID" 2>/dev/null || true
    echo "  Started PID ${PID} — log at /tmp/syke-docs-preview.log"
    echo "  Stop with: kill ${PID}"
    echo
    ;;
  -h|--help)
    sed -n '2,20p' "${BASH_SOURCE[0]}"
    ;;
  *)
    banner
    exec python3 -m http.server "$PORT" --bind "$BIND"
    ;;
esac
