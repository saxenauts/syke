# Security Policy

## Credential Management

Syke handles API keys and OAuth tokens for multiple platforms. All credentials are managed via environment variables or external config files — **never hardcoded or committed to the repository**.

| Credential | Source | Storage |
|-----------|--------|---------|
| `ANTHROPIC_API_KEY` | Anthropic Console | `.env` file (gitignored) |
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
├── profile.json     # Latest identity profile (gitignored)
├── metrics.jsonl    # Operational metrics (gitignored)
└── syke.log         # Application log (gitignored)
```

No data is sent to external services except Anthropic's API for LLM inference. The API calls contain event content for extraction and perception — review Anthropic's [data usage policy](https://www.anthropic.com/policies) for details.

## Reporting Security Issues

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue.
2. Email the maintainer directly (see repo owner profile).
3. Include steps to reproduce and potential impact.

## Pre-Commit Verification

Before committing, verify no secrets are staged:

```bash
# Check for common secret patterns in staged files
git diff --cached --name-only | xargs grep -l -i "sk-ant-\|ghp_\|password\|secret" 2>/dev/null
```

If this returns any files, review them before committing.
