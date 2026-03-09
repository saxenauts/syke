# Security Policy

This document describes how Syke handles credentials, local data, and outbound data flow. It is intentionally explicit about current behavior and security boundaries.

## Security Model

Syke is designed as a local-first system. Core state (events, memories, memex files, config, and provider credentials) is stored on the local filesystem.

Credential protection for Syke-managed provider tokens is filesystem-permission-based, not encryption-based:

- `~/.syke/auth.json` stores tokens as plaintext JSON.
- File mode is set to `0600` (owner read/write only).
- Writes are atomic (temporary file in the same directory, then rename).
- Advisory file locking (`flock`) is used during reads/writes to reduce CLI/daemon races.

If an attacker can read files as the same OS user account, they can read these tokens. Syke currently does not add an additional at-rest encryption layer for `~/.syke/auth.json`.

## Credential Storage

### LLM Providers

| Provider ID | Credential Source | Storage Location | Notes |
|-------------|-------------------|------------------|-------|
| `claude-login` | Claude session auth | `~/.claude/` | Managed by `claude login`; no Syke-managed API key |
| `codex` | Codex session auth | `~/.codex/auth.json` | Managed by `codex login`; Syke reads token and may refresh it via Codex flow |
| `openrouter` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |
| `zai` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |
| `kimi` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |

### Platform Credentials

| Credential | Typical Source | Storage Location |
|------------|----------------|------------------|
| `GITHUB_TOKEN` | GitHub Developer Settings | Environment / `.env` (gitignored) |
| Gmail OAuth client credentials | Google Cloud Console | `~/.config/syke/gmail_credentials.json` |
| Gmail OAuth access/refresh token | OAuth local consent flow | `~/.config/syke/gmail_token.json` |

## Local Data Storage

Default data root is `~/.syke/data`. Per-user state is written to:

- `~/.syke/data/{user}/syke.db` (SQLite timeline and memory state)
- `~/.syke/data/{user}/CLAUDE.md`
- `~/.syke/data/{user}/metrics.jsonl`
- `~/.syke/data/{user}/syke.log`

These paths can be overridden via config/environment, but remain local filesystem paths.

## Privacy Filters (Pre-ingestion)

Before events are inserted into the local database, Syke runs content filtering:

- `redact_credentials = true`: attempts to sanitize known credential patterns (for example API keys, bearer tokens, some password formats, private key blocks, and credentialed connection strings) by replacing matches with `[REDACTED]`.
- `skip_private_messages = true`: skips events that look dominated by private-message transcript patterns (for example copied WhatsApp/iMessage/Telegram chat logs).

Current default policy is defined in `~/.syke/config.toml` under `[privacy]`:

```toml
[privacy]
redact_credentials = true
skip_private_messages = true
```

Operationally, these filters run prior to database insertion and therefore reduce sensitive content persistence in `syke.db`.

## Data Egress: What Leaves the Machine

By default, Syke stores and processes data locally. Data leaves the machine only when calling external services.

Primary outbound path:

- LLM API calls used for synthesis/rebuild/ask operations, sent to the configured provider (`claude-login`, `codex`, `openrouter`, `zai`, or `kimi`).

Ingestion adapters can also call provider APIs for data retrieval when configured (for example Gmail or GitHub), but ingested data is stored locally after retrieval.

## Repository and Operational Hygiene

- Do not commit `.env` files or credential exports.
- Do not commit OAuth token files.
- Keep OAuth files outside the repository (`~/.config/syke/`).
- Review staged changes for accidental secrets before commit.

## Reporting Security Issues

If you discover a vulnerability:

1. Do not open a public issue with exploit details.
2. Contact the maintainer directly.
3. Include impact, reproduction steps, and affected versions/commit.
