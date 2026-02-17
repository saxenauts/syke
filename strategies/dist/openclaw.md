# Distribution Strategy: OpenClaw (MCP Skill)

## Overview
Syke integrates with OpenClaw as an MCP skill — the native way OpenClaw extends agent capabilities. The agent gets live access to identity profile, timeline queries, and event push via Syke's 7 MCP tools.

## Prerequisites
- Python 3.12+ with Syke installed (`pip install -e .` in the Syke repo)
- At least one data source ingested (`python -m syke --user <USER_ID> setup --yes`)
- `ANTHROPIC_API_KEY` set in environment or `.env`

## Agent Self-Install

An OpenClaw agent can set up Syke automatically:

```bash
# 1. Clone and install Syke
git clone https://github.com/saxenauts/syke.git ~/syke
cd ~/syke && python3 -m venv .venv && source .venv/bin/activate && pip install -e .

# 2. Set API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Run the full pipeline (detect sources, ingest, perceive)
python -m syke --user <USER_ID> setup --yes

# 4. Create the OpenClaw skill (see Skill Setup below)
mkdir -p <workspace>/skills/syke
# Write SKILL.md content from the template below
```

## Skill Setup

Create `<workspace>/skills/syke/SKILL.md`:

```markdown
---
name: syke
description: Personal context daemon — provides identity profile, timeline, and event push/query via MCP
---

# Syke — Personal Context

Syke ingests your digital footprint and perceives who you are. Use these tools to read context and push signals.

## Config

\```json
{
  "mcpServers": {
    "syke": {
      "command": "<PATH_TO_SYKE>/.venv/bin/python",
      "args": ["-m", "syke", "--user", "<USER_ID>", "serve", "--transport", "stdio"],
      "env": {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}"
      }
    }
  }
}
\```

## Tools

| Tool | Purpose |
|------|---------|
| `get_profile(format)` | Identity profile (json, markdown, claude-md, user-md) |
| `query_timeline(since, source, limit, summary)` | Query events (summaries by default) |
| `get_event(event_id)` | Fetch full content for a single event |
| `get_manifest()` | Data summary and status |
| `search_events(query, limit, summary)` | Full-text search (summaries by default) |
| `push_event(source, event_type, title, content)` | Push a signal from this session |
| `push_events(events_json)` | Batch push |

## When to Use

- Call `get_profile()` at session start to understand who you're talking to
- Call `push_event()` when something meaningful happens (decision, preference, project start)
- Call `query_timeline()` or `search_events()` for recent context
- Call `get_manifest()` to see what data sources are available
```

Replace `<PATH_TO_SYKE>` with the absolute path to the Syke repo and `<USER_ID>` with the user's ID.

## Alternative: Global Install

To make Syke available to all OpenClaw agents (not just one workspace):

```bash
mkdir -p ~/.openclaw/skills/syke
# Copy the SKILL.md template above to ~/.openclaw/skills/syke/SKILL.md
```

Skill precedence: `<workspace>/skills` > `~/.openclaw/skills` > bundled skills.

## Verification

Once installed, confirm the tools are available:

1. Start an OpenClaw session in the workspace with the skill
2. Ask the agent: "What MCP tools do you have from Syke?"
3. Expected: all 7 tools listed (get_profile, query_timeline, get_event, get_manifest, search_events, push_event, push_events)
4. Test: ask the agent to call `get_profile("markdown")` — should return the user's identity profile

## Known Issues

- **One-way push**: Events pushed from OpenClaw via `push_event` are stored in Syke's DB, but OpenClaw session transcripts are not auto-ingested as a data source yet
- **Manual path config**: The `<PATH_TO_SYKE>` in SKILL.md must be an absolute path — no `~` expansion in MCP server configs
