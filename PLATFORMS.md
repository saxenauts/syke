# Syke Platform Support

## Ingestion (data into Syke)

| Platform | Strategy File | Status |
|----------|--------------|--------|
| Claude Code | descriptor + local/runtime adapter path | Active |
| ChatGPT | export ingestion path | Active |
| Codex | descriptor + local/runtime adapter path | Active |
| GitHub | historical/docs reference | Experimental |
| Gmail | historical/docs reference | Experimental |
| Twitter/X | `strategies/ingest/twitter-browser.md` | Planned |
| YouTube | `strategies/ingest/youtube-browser.md` | Planned |

## Distribution (Syke into platforms)

| Platform | Integration Path | Status |
|----------|------------------|--------|
| Claude Code | memex render via `CLAUDE.md` include | Active |
| Claude Desktop | trusted-folder adapter | Partial |
| Codex | skill/file distribution path | Experimental |
| Cursor | skill/file distribution path | Experimental |
| Windsurf | skill/file distribution path | Experimental |
| Hermes | harness adapter | Active |

## Adding a Platform

Agents should update this table when they:
- Add or validate a real adapter/runtime path
- Wire or validate a concrete distribution path under `syke/distribution/`
- Promote an experimental platform to active

Updated by agents as they self-heal and add new platforms.
