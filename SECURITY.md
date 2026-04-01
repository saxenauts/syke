# Security Policy

This document describes how Syke handles credentials, local data, and outbound data flow. It is intentionally explicit about current behavior and security boundaries.

## Security Model

Syke is designed as a local-first system. Core state (events, memex render targets, config, metrics, logs, and provider credentials) is stored on the local filesystem.

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
| `codex` | Codex session auth | `~/.codex/auth.json` | Managed by `codex login`; Syke reads session state from Codex |
| `openrouter` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |
| `zai` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |
| `kimi` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |
| `openai` | API key | `~/.syke/auth.json` | Stored as plaintext JSON, protected by local file permissions |
| `azure` | API key | `~/.syke/auth.json` | Endpoint/model settings live in `~/.syke/config.toml` |
| `ollama` | No credential by default | none | Local provider; base URL/model settings live in `~/.syke/config.toml` |
| `vllm` | Optional API key | `~/.syke/auth.json` when used | Base URL/model settings live in `~/.syke/config.toml` |
| `llama-cpp` | Optional API key | `~/.syke/auth.json` when used | Base URL/model settings live in `~/.syke/config.toml` |

### Platform Credentials

| Credential | Typical Source | Storage Location |
|------------|----------------|------------------|
| `GITHUB_TOKEN` | GitHub Developer Settings | Environment / `.env` (gitignored) |

## Local Data Storage

Default data root is `~/.syke/data`. Per-user state is written to:

- `~/.syke/data/{user}/events.db` (immutable observed-event ledger)
- `~/.syke/data/{user}/syke.db` (mutable learned-memory store)
- `~/.syke/data/{user}/CLAUDE.md`
- `~/.syke/data/{user}/metrics.jsonl`
- `~/.syke/data/{user}/syke.log`

Pi also gets a workspace with routed copies or bindings such as `events.db`, `syke.db`, and `MEMEX.md`. The workspace is a runtime/distribution surface, not the source of truth.

These paths can be overridden via config/environment, but remain local filesystem paths.

## Privacy Filters (Pre-ingestion)

Before events are inserted into the local database, Syke runs content filtering:

- Credential redaction is always on: Syke attempts to sanitize known credential patterns (for example API keys, bearer tokens, some password formats, private key blocks, and credentialed connection strings) by replacing matches with `[REDACTED]`.
- Private-message skipping is always on: Syke skips events that look dominated by private-message transcript patterns (for example copied WhatsApp/iMessage/Telegram chat logs).

These filters are runtime behavior in the observe/content-filter path. They are not currently exposed as a typed top-level config section in the 0.5 config model.

## Data Egress: What Leaves the Machine

By default, Syke stores and processes data locally. Data leaves the machine only when calling external services.

Primary outbound path:

- LLM API calls used for synthesis/rebuild/ask operations, sent to the configured provider (`codex`, `openrouter`, `zai`, `kimi`, `openai`, `azure`, `ollama`, `vllm`, or `llama-cpp`).

Some adapters may call external provider APIs when configured, but the current 0.5 branch is primarily centered on local observation paths.

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
