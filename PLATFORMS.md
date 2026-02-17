# Syke Platform Support

## Ingestion (data into Syke)

| Platform | Strategy File | Status |
|----------|--------------|--------|
| Claude Code | `strategies/ingest/claude-code.md` | Verified (built-in adapter) |
| ChatGPT | `strategies/ingest/chatgpt-export.md` | Verified |
| GitHub | `strategies/ingest/github-api.md` | Verified |
| Gmail | `strategies/ingest/gmail-oauth.md` | Verified |
| Twitter/X | `strategies/ingest/twitter-browser.md` | Planned |
| YouTube | `strategies/ingest/youtube-browser.md` | Planned |

## Distribution (Syke into platforms)

| Platform | Strategy File | Status |
|----------|--------------|--------|
| Claude Code | `strategies/dist/claude-code.md` | Verified |
| Claude Desktop | `strategies/dist/claude-desktop.md` | Verified |

## Adding a Platform

Agents should update this table when they:
- Add a new ingestion adapter and strategy file
- Register Syke on a new platform and write a distribution strategy

Updated by agents as they self-heal and add new platforms.
