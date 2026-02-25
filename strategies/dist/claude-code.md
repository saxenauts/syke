# Distribution Strategy: Claude Code

## Overview
Register Syke as an MCP server in Claude Code so it can read user context and push events back.

## Config Location
- Project-level: `.mcp.json` in the project root
- Global: `~/.claude/settings.json` under `mcpServers`

## MCP Server Command
```json
{
  "mcpServers": {
    "syke": {
      "command": "python",
      "args": ["-m", "syke", "--user", "<USER_ID>", "serve", "--transport", "stdio"],
      "cwd": "<PATH_TO_SYKE_REPO>"
    }
  }
}
```

## Prerequisites
- Python 3.12+ with venv activated
- `pip install -e .` from the syke repo root (the module must be importable)

## Known Issues
- `cwd` field is not always honored by Claude Code — the `syke` package must be pip-installed into the active Python environment
- If you see "No module named syke", run `pip install -e <path-to-syke-repo>`
- Restart Claude Code after changing MCP config

## Verification
After registering, the following 3 MCP tools should appear:
- `get_live_context` — read identity profile (json, markdown, claude-md, user-md)
- `ask` — natural language questions about the user (explores timeline agenically)
- `record` — push an observation from this session into the user's timeline

## Last Verified
2026-02-21
