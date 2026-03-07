# Syke Platform Support

## Ingestion (data into Syke)

| Platform | Strategy File | Status |
|----------|--------------|--------|
| Claude Code | `strategies/ingest/claude-code.md` | Verified (built-in adapter) |
| ChatGPT | `strategies/ingest/chatgpt-export.md` | Verified |
| Codex | `syke ingest codex` | Verified |
| GitHub | `strategies/ingest/github-api.md` | Verified |
| Gmail | `strategies/ingest/gmail-oauth.md` | Verified |
| Twitter/X | `strategies/ingest/twitter-browser.md` | Planned |
| YouTube | `strategies/ingest/youtube-browser.md` | Planned |

## Distribution (Syke into platforms)

| Platform | Integration Path | Status |
|----------|------------------|--------|
| Claude Code | `syke/distribution/context_files.py` | Verified |
| Claude Desktop | `syke/distribution/harness/claude_desktop.py` | Verified |
| Codex | `syke/distribution/context_files.py` | Verified |
| Cursor | `syke/distribution/context_files.py` | Verified |
| Windsurf | `syke/distribution/context_files.py` | Verified |
| Hermes | `syke/distribution/harness/hermes.py` | Verified |

## Adding a Platform

Agents should update this table when they:
- Add a new ingestion adapter and strategy file
- Register Syke on a new platform and wire distribution under `syke/distribution/`

Updated by agents as they self-heal and add new platforms.
