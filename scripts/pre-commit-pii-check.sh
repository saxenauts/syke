#!/usr/bin/env bash
# Pre-commit hook: scans staged files for PII, secrets, and sensitive data.
# Install: cp scripts/pre-commit-pii-check.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
NC='\033[0m'

FAILED=0

echo "Running PII & secrets scan on staged files..."

# Get list of staged files (excluding deleted files and self-referential config)
STAGED=$(git diff --cached --name-only --diff-filter=d | grep -v '^\.pii-patterns$' | grep -v '^scripts/pre-commit' | grep -v '^\.env\.example$' || true)

if [ -z "$STAGED" ]; then
    echo -e "${GREEN}No staged files to check.${NC}"
    exit 0
fi

# --- Category 1: Secrets (HARD BLOCK) ---
# API keys, tokens, passwords that should NEVER be committed
SECRETS_PATTERNS=(
    'sk-ant-api[0-9]+-[A-Za-z0-9_-]{20,}'    # Anthropic API keys
    'sk-[A-Za-z0-9]{20,}'                      # OpenAI API keys
    'ghp_[A-Za-z0-9]{36,}'                     # GitHub tokens
    'gho_[A-Za-z0-9]{36,}'                     # GitHub OAuth tokens
    'xoxb-[A-Za-z0-9-]+'                       # Slack tokens
    'AIza[A-Za-z0-9_-]{35}'                    # Google API keys
    'AKIA[A-Z0-9]{16}'                         # AWS access keys
)

for pattern in "${SECRETS_PATTERNS[@]}"; do
    MATCHES=$(echo "$STAGED" | xargs git diff --cached -- | grep -E "$pattern" | grep '^+' | grep -v '^+++' || true)
    if [ -n "$MATCHES" ]; then
        echo -e "${RED}BLOCKED: API key/token pattern detected:${NC}"
        echo "$MATCHES" | head -3
        FAILED=1
    fi
done

# --- Category 2: PII Patterns (HARD BLOCK) ---
# These are configurable per-project. Add patterns for YOUR sensitive data.
PII_FILE=".pii-patterns"
if [ -f "$PII_FILE" ]; then
    while IFS= read -r pattern || [ -n "$pattern" ]; do
        # Skip comments and empty lines
        [[ "$pattern" =~ ^#.*$ ]] && continue
        [[ -z "$pattern" ]] && continue

        MATCHES=$(echo "$STAGED" | xargs git diff --cached -- | grep -iE "$pattern" | grep '^+' | grep -v '^+++' || true)
        if [ -n "$MATCHES" ]; then
            echo -e "${RED}BLOCKED: PII pattern '$pattern' detected:${NC}"
            echo "$MATCHES" | head -3
            FAILED=1
        fi
    done < "$PII_FILE"
fi

# --- Category 3: Common dangerous patterns (WARNING) ---
WARN_PATTERNS=(
    'password\s*[=:]\s*["\x27][^\s"]{8,}'     # Hardcoded passwords
    '/Users/[a-z]+/'                            # Hardcoded home paths
    '\b[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b'  # IP addresses
)

for pattern in "${WARN_PATTERNS[@]}"; do
    MATCHES=$(echo "$STAGED" | xargs git diff --cached -- | grep -E "$pattern" | grep '^+' | grep -v '^+++' || true)
    if [ -n "$MATCHES" ]; then
        echo -e "${YELLOW}WARNING: Potentially sensitive pattern detected:${NC}"
        echo "$MATCHES" | head -3
        echo -e "${YELLOW}Review before committing. Use --no-verify to bypass.${NC}"
        # Warnings don't block, but are visible
    fi
done

if [ $FAILED -ne 0 ]; then
    echo ""
    echo -e "${RED}COMMIT BLOCKED: Secrets or PII detected in staged files.${NC}"
    echo -e "${RED}Fix the issues above, or use --no-verify to bypass (NOT recommended).${NC}"
    exit 1
fi

echo -e "${GREEN}PII & secrets scan passed.${NC}"
exit 0
