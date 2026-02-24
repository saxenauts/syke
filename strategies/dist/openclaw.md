# Distribution Strategy: OpenClaw (MCP Skill)

## Overview
Syke integrates with OpenClaw as an MCP skill — the native way OpenClaw extends agent capabilities. The agent gets live access to identity profile, natural language questions, and event recording via Syke's 3 MCP tools.

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

# 3. Run the full pipeline (detect sources, ingest, synthesize)
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
description: Personal context daemon — provides identity profile, ask, and record via MCP
---

# Syke — Personal Context

Syke ingests your digital footprint and synthesizes a living memory of who you are. Use these tools to read context and push signals.

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
| `get_live_context(format)` | Identity profile (json, markdown, claude-md, user-md) |
| `ask(question)` | Natural language questions about the user — explores timeline agenically |
| `record(observation)` | Push an observation from this session into the user's timeline |

## When to Use

- Call `get_live_context()` at session start to understand who you're talking to
- Call `ask()` to explore user history — "What did they work on last week?"
- Call `record()` when something meaningful happens (decision, preference, project start)
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
3. Expected: 3 tools listed (get_live_context, ask, record)
4. Test: ask the agent to call `get_live_context("markdown")` — should return the user's identity profile

## Known Issues

 **One-way push**: Events pushed from OpenClaw via `record` are stored in Syke's DB, but OpenClaw session transcripts are not auto-ingested as a data source yet
- **Manual path config**: The `<PATH_TO_SYKE>` in SKILL.md must be an absolute path — no `~` expansion in MCP server configs
