# Security Policy

This document describes how Syke handles credentials, local data, and outbound data flow. It is intentionally explicit about current behavior and security boundaries.

## Security Model

Syke is designed as a local-first system. Core state (events, memex render targets, config, metrics, logs, and provider credentials) is stored on the local filesystem.

Credential protection for Syke-managed provider tokens is filesystem-permission-based, not encryption-based:

- `~/.syke/pi-agent/auth.json` stores tokens as plaintext JSON.
- File mode is set to `0600` (owner read/write only).
- Writes are atomic (temporary file in the same directory, then rename).
- All credential mutations are audit-logged to `~/.config/syke/pi-state-audit.log`.

If an attacker can read files as the same OS user account, they can read these tokens. Syke currently does not add an additional at-rest encryption layer for `~/.syke/pi-agent/auth.json`.

## Credential Storage

### LLM Providers

| Provider ID | Credential Source | Storage Location | Notes |
|-------------|-------------------|------------------|-------|
| Pi-native OAuth providers | OAuth token via Pi login flow | `~/.syke/pi-agent/auth.json` | Managed by `syke auth login`; stored as `{"type": "oauth", ...}` |
| API-key providers (openrouter, zai, kimi, openai, etc.) | API key | `~/.syke/pi-agent/auth.json` | Stored as `{"type": "api_key", "key": "..."}`, protected by local file permissions |
| Custom OpenAI-compatible providers | Optional API key | `~/.syke/pi-agent/auth.json` | Base URL and model overrides in `~/.syke/pi-agent/models.json` |
| Local providers (ollama, vllm, llama-cpp) | No credential by default | none | Base URL/model overrides in `~/.syke/pi-agent/models.json` |

Active provider and model selections are stored in `~/.syke/pi-agent/settings.json`. Provider endpoint overrides live in `~/.syke/pi-agent/models.json`. Syke does not maintain a hardcoded provider list; available providers come from Pi's live catalog.

### Platform Credentials

| Credential | Typical Source | Storage Location |
|------------|----------------|------------------|
| `GITHUB_TOKEN` | GitHub Developer Settings | Environment / `.env` (gitignored) |

## Local Data Storage

All state lives under `~/.syke/`:

- `~/.syke/syke.db` (single database: memories, links, events, cycles, rollout traces)
- `~/.syke/MEMEX.md` (exported memex projection)
- `~/.syke/adapters/` (harness adapter markdowns)
- `~/.syke/sessions/` (session logs)

These paths can be overridden via config/environment, but remain local filesystem paths.

## Privacy Filters (Pre-ingestion)

Before events are inserted into the local database, Syke runs content filtering:

- Credential redaction is always on: Syke attempts to sanitize known credential patterns (for example API keys, bearer tokens, some password formats, private key blocks, and credentialed connection strings) by replacing matches with `[REDACTED]`.
- Private-message skipping is always on: Syke skips events that look dominated by private-message transcript patterns (for example copied WhatsApp/iMessage/Telegram chat logs).

These filters are runtime behavior in the observe/content-filter path. They are not currently exposed as a typed top-level config section in the 0.5 config model.

## Data Egress: What Leaves the Machine

By default, Syke stores and processes data locally. Data leaves the machine only when calling external services.

Primary outbound path:

- LLM API calls used for synthesis and ask operations, sent to the configured provider. Available providers come from Pi's live catalog (e.g. `openrouter`, `zai`, `kimi-coding`, `openai`, `azure-openai-responses`, or custom OpenAI-compatible endpoints).

## OS Sandbox

Every Pi process (ask and synthesis) runs inside a macOS seatbelt sandbox with deny-default reads. The profile is generated per user at launch time from the harness catalog.

**Filesystem:**
- Reads: deny-default. Only catalog-known harness directories, system runtime paths, `~/.syke/`, and temp are allowed.
- Writes: `~/.syke/` and temp only. The agent cannot write outside its home.
- Sensitive paths (`.ssh`, `.gnupg`, `.aws`, `.azure`, `.docker`, `.kube`, `.config/gcloud`) have explicit deny rules as defense-in-depth — they override any accidental broad allows.

**Network:**
- Port-restricted outbound: HTTPS (443), HTTP (80), DNS (53), localhost only.
- Arbitrary remote connections on non-standard ports are blocked at kernel level.
- Daemon IPC uses Unix domain sockets (filesystem), not the network stack.

The sandbox is enforced by macOS sandbox-exec (kernel-level). The agent process cannot bypass it. Disable with `SYKE_DISABLE_SANDBOX=1` for debugging.

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
