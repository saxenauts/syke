# Security Policy

## Credential Management

Syke handles LLM provider credentials, API keys, and OAuth tokens. All credentials are managed via external config files or environment variables — **never hardcoded or committed to the repository**.

**LLM Provider Credentials**:
| Provider | Storage | Notes |
|----------|---------|-------|
| Claude Code | Session auth via `~/.claude/` | Managed by `claude login`, no API key |
| Codex (ChatGPT Plus) | `~/.codex/auth.json` | Managed by codex CLI |
| OpenRouter | `~/.syke/auth.json` | API key stored encrypted |
| Zai | `~/.syke/auth.json` | API key stored encrypted |

**Platform Credentials**:
| Credential | Source | Storage |
|-----------|--------|---------|
| `GITHUB_TOKEN` | GitHub Settings → Developer settings | `.env` file (gitignored) |
| Gmail OAuth credentials | Google Cloud Console | `~/.config/syke/` (outside repo) |
| Gmail OAuth token | Generated at first run | `~/.config/syke/` (outside repo) |

### Rules

1. **Never commit `.env` files.** The `.gitignore` blocks `.env` and `.env.*` (except `.env.example`).
2. **Never commit credentials or tokens.** The `.gitignore` blocks `credentials.json`, `*_token.json`, and `*.token`.
3. **User data stays local.** The `data/` directory (SQLite databases, profiles, logs, metrics) is gitignored.
4. **OAuth tokens live outside the repo** in `~/.config/syke/`, not in the project directory.

## Data Privacy

Syke stores all user data locally on disk:

```
data/{user_id}/
├── syke.db          # SQLite event timeline (gitignored)
├── metrics.jsonl    # Operational metrics (gitignored)
└── syke.log         # Application log (gitignored)
```

Syke supports multiple LLM providers. Provider credentials are stored in `~/.syke/auth.json` (API keys encrypted at rest). Codex tokens are read from `~/.codex/auth.json` (managed by codex CLI). Claude Code uses session auth via `~/.claude/` (no API key stored).

No data is sent to external services except the active LLM provider for inference. API calls contain event content for memory synthesis — review your provider's data usage policy (Anthropic, OpenAI, OpenRouter, Zai).

## Reporting Security Issues

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue.
2. Email the maintainer directly (see repo owner profile).
3. Include steps to reproduce and potential impact.

## Pre-Commit Verification

Before committing, verify no secrets are staged:

```bash
# Check for common secret patterns in staged files
git diff --cached --name-only | xargs grep -l -i "ghp_\|password\|secret" 2>/dev/null
```

If this returns any files, review them before committing.
