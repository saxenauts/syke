#!/usr/bin/env bash
# Local release-candidate gate.
#
# This is the pre-push proof step. GitHub Actions should confirm a candidate
# that already passed locally; it should not be the first place release bugs
# are discovered.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ALLOW_DIRTY=false
RUN_PREFLIGHT=true
RUN_LOCAL_WEB=true
RUN_LINUX_PRODUCT_QA=false
RUN_LINUX_MANAGED_SERVICE=false
PROVIDER_STATE=""
TAG_NAME=""

usage() {
  cat <<'EOF'
usage: scripts/release-candidate.sh [options]

Default gate:
  - require a clean git worktree
  - run scripts/release-preflight.sh
  - verify local loopback /api/health and /api/timeline if the daemon is serving
  - print version/tag/publication state

Options:
  --allow-dirty                 allow a dirty tree while developing this script
  --skip-preflight              skip scripts/release-preflight.sh
  --skip-local-web              skip local loopback web API smoke
  --with-linux-product-qa       run Dockerized Linux product QA on the built wheel
  --provider-state <dir>        provider state for Linux product QA
  --with-linux-managed-service  run Linux user-systemd smoke on the current host
  --for-tag <vX.Y.Z>            verify package version matches the intended tag
  -h, --help                    show this help

Release order:
  1. Run this script locally before pushing.
  2. Push only after it passes.
  3. Let GitHub Actions confirm the pushed commit.
  4. Bump version/changelog, run this script with --for-tag, then tag/publish.
EOF
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    echo "$flag requires a value" >&2
    usage >&2
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-dirty)
      ALLOW_DIRTY=true
      shift
      ;;
    --skip-preflight)
      RUN_PREFLIGHT=false
      shift
      ;;
    --skip-local-web)
      RUN_LOCAL_WEB=false
      shift
      ;;
    --with-linux-product-qa)
      RUN_LINUX_PRODUCT_QA=true
      shift
      ;;
    --provider-state)
      require_value "$1" "${2:-}"
      PROVIDER_STATE="${2:-}"
      shift 2
      ;;
    --with-linux-managed-service)
      RUN_LINUX_MANAGED_SERVICE=true
      shift
      ;;
    --for-tag)
      require_value "$1" "${2:-}"
      TAG_NAME="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "$REPO_DIR"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "[candidate] missing required command: $1" >&2
    exit 1
  fi
}

step() {
  echo
  echo "[candidate] $*"
}

need_cmd git
need_cmd uv

step "repo: $REPO_DIR"
git status --short --branch

if [[ "$ALLOW_DIRTY" != true ]]; then
  if [[ -n "$(git status --porcelain=v1)" ]]; then
    echo "[candidate] dirty worktree; commit or stash before freezing a release candidate." >&2
    exit 1
  fi
fi

if [[ -n "$TAG_NAME" ]]; then
  step "checking intended release tag: $TAG_NAME"
  uv run python "$SCRIPT_DIR/check_release_tag.py" "$TAG_NAME"
  if git rev-parse -q --verify "refs/tags/$TAG_NAME" >/dev/null; then
    tagged_commit="$(git rev-list -n 1 "$TAG_NAME")"
    head_commit="$(git rev-parse HEAD)"
    if [[ "$tagged_commit" != "$head_commit" ]]; then
      echo "[candidate] tag $TAG_NAME already exists on $tagged_commit, not HEAD $head_commit" >&2
      exit 1
    fi
  fi
fi

if [[ "$RUN_PREFLIGHT" == true ]]; then
  step "running local release preflight"
  bash "$SCRIPT_DIR/release-preflight.sh"
else
  step "skipping local release preflight"
fi

wheel_path="$(uv run python - <<'PY'
from pathlib import Path
import tomllib

version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
wheel_version = version.replace("-", "_")
wheels = sorted(Path("dist").glob(f"syke-{wheel_version}-*.whl"))
print(wheels[0].resolve() if wheels else "")
PY
)"

if [[ -z "$wheel_path" && "$RUN_LINUX_PRODUCT_QA" == true ]]; then
  echo "[candidate] Linux product QA needs a built wheel; run preflight or build first." >&2
  exit 1
fi

if [[ "$RUN_LOCAL_WEB" == true ]]; then
  step "checking local timeline API"
  uv run python - <<'PY'
import json
import os
import sys
import urllib.request

port = os.getenv("SYKE_WEB_PORT", "8765")
base = f"http://127.0.0.1:{port}"

try:
    with urllib.request.urlopen(f"{base}/api/health", timeout=5) as response:
        health = json.load(response)
    with urllib.request.urlopen(f"{base}/api/timeline?days=7", timeout=5) as response:
        timeline = json.load(response)
except Exception as exc:
    print(f"[candidate] local timeline API not reachable at {base}: {exc}", file=sys.stderr)
    raise SystemExit(1)

if health.get("db_present") is not True:
    print(f"[candidate] /api/health did not report db_present=true: {health}", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(timeline.get("events"), list):
    print(f"[candidate] /api/timeline did not return events: {timeline}", file=sys.stderr)
    raise SystemExit(1)

print(
    "[candidate] local timeline API ok: "
    f"events={len(timeline['events'])} setup_blocker={health.get('setup_blocker')}"
)
PY
else
  step "skipping local timeline API smoke"
fi

if [[ "$RUN_LINUX_PRODUCT_QA" == true ]]; then
  step "running Dockerized Linux product QA"
  args=(--wheel "$wheel_path")
  if [[ -n "$PROVIDER_STATE" ]]; then
    args+=(--provider-state "$PROVIDER_STATE")
  else
    args+=(--allow-no-provider)
  fi
  bash "$SCRIPT_DIR/linux-product-qa.sh" "${args[@]}"
else
  step "skipping Linux product QA"
fi

if [[ "$RUN_LINUX_MANAGED_SERVICE" == true ]]; then
  step "running Linux managed-service smoke on this host"
  bash "$SCRIPT_DIR/linux-managed-service-smoke.sh"
else
  step "skipping Linux managed-service smoke"
fi

step "candidate summary"
uv run python - <<'PY'
import tomllib
from pathlib import Path

version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
print(f"[candidate] package_version={version}")
PY
echo "[candidate] git_head=$(git rev-parse --short HEAD)"
echo "[candidate] git_describe=$(git describe --tags --dirty --always)"
if [[ -n "$wheel_path" ]]; then
  echo "[candidate] wheel=$wheel_path"
fi
echo "[candidate] passed"
