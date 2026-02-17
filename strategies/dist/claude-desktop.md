# Distribution Strategy: Claude Desktop

## Overview
Register Syke as an MCP server in the Claude Desktop app so it can provide user context during conversations.

## Config Location
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

## MCP Server Config
Add to the `mcpServers` key (create if missing). Preserve any existing keys (especially `preferences`):

```json
{
  "mcpServers": {
    "syke": {
      "command": "python3",
      "args": ["-m", "syke", "--user", "<USER_ID>", "serve", "--transport", "stdio"],
      "cwd": "<PATH_TO_SYKE_REPO>"
    }
  }
}
```

## Prerequisites
- Python 3.12+ with syke installed: `pip install -e <path-to-syke-repo>`
- `ANTHROPIC_API_KEY` set in the syke `.env` file
- At least one data source ingested (`syke setup --user <name> --yes`)

## Known Issues
- `cwd` field is **not honored** by Claude Desktop — the syke package must be pip-installed system-wide or in the Python that `python3` resolves to
- Must **restart Claude Desktop** after config changes (Cmd+Q, relaunch)
- "No module named syke" means pip install was not done or was done in a different Python
- The config file may not exist on first run — create it with the full JSON structure

## Verification
After restart, open a new Claude Desktop conversation. You should see Syke's tools listed. Test with: "What tools do you have from Syke?"

## Last Verified
2026-02-12
