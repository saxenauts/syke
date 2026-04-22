# Syke Platform Support

## Ingestion (data into Syke)

| Platform | Local Artifact Contract | Status |
|----------|-------------------------|--------|
| Claude Code | `~/.claude/projects/**/*.jsonl`, `~/.claude/transcripts/*.jsonl` | Active |
| Codex | rollout JSONL under `~/.codex/sessions` / `archived_sessions`, plus `session_index.jsonl` and SQLite metadata | Active |
| OpenCode | SQLite DB under `~/.local/share/opencode/*.db`, including channel-named DBs | Active |
| Cursor | official user-data roots under Cursor `workspaceStorage` / `globalStorage` | Active |
| GitHub Copilot | Copilot CLI `~/.copilot/session-state/**/events.jsonl` plus VS Code `chatSessions` files | Active |
| Antigravity | workflow artifacts under `~/.gemini/antigravity/brain` and browser recording metadata | Active |
| Hermes | `~/.hermes/state.db` plus session JSON under `~/.hermes/sessions` | Active |
| Gemini CLI | `~/.gemini/tmp/<project_hash>/chats/**/*.json` and checkpoint JSON | Active |
| GitHub | historical/docs reference | Experimental |

## Distribution (Syke into agents)

Syke currently supports only three distribution surfaces:

| Surface | Path | Status |
|---------|------|--------|
| CLI | `syke ask`, `syke memex`, `syke record`, `syke doctor`, `syke setup` | Active |
| MEMEX artifact | exported memex at `~/.syke/MEMEX.md` | Active |
| Capability registration | canonical Syke capability package installed to detected skill/capability surfaces, plus native wrappers where needed | Active |

## Adding a Platform

Agents should update this table when they:
- Add or validate a real adapter/runtime path
- Fit a new agent into one of the three supported distribution surfaces
- Promote an experimental ingestion path to active

For current active harnesses, setup is seed-first:

- Syke ships seed adapters in-repo for the active catalog
- setup validates and deploys the shipped seed locally
- the Observe factory is the repair/new-harness fallback when the shipped seed does not fit the detected local artifact shape

Updated by agents as they self-heal and add new platforms.
