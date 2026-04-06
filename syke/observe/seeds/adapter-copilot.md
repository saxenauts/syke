# copilot

GitHub Copilot operates in two modes: a CLI agent (`copilot-cli`) that runs in the terminal, and a VS Code extension that provides chat within the editor. The CLI stores sessions as JSONL event logs alongside YAML workspace metadata. The VS Code extension stores chat sessions as JSON files in workspace storage directories.

## Where

CLI sessions:
```
~/.copilot/session-state/*/events.jsonl
~/.copilot/session-state/*/workspace.yaml
```

VS Code chat sessions (macOS):
```
~/Library/Application Support/Code/User/workspaceStorage/**/chatSessions/*.json
~/Library/Application Support/Code/User/workspaceStorage/**/chatSessions/*.jsonl
~/Library/Application Support/Code/User/globalStorage/emptyWindowChatSessions/*.json
~/Library/Application Support/Code/User/globalStorage/emptyWindowChatSessions/*.jsonl
```

VS Code chat sessions (Linux):
```
~/.config/Code/User/workspaceStorage/**/chatSessions/*.json
~/.config/Code/User/workspaceStorage/**/chatSessions/*.jsonl
~/.config/Code/User/globalStorage/emptyWindowChatSessions/*.json
~/.config/Code/User/globalStorage/emptyWindowChatSessions/*.jsonl
```

The adapter also checks for a `session-store.db` SQLite file at `~/.copilot/session-store.db` for supplementary metadata.

## Sessions

**CLI sessions**: Each subdirectory under `session-state/` is one session. The directory name is the session ID. The `events.jsonl` file contains the event log. An optional `workspace.yaml` provides workspace context.

**VS Code sessions**: One JSON file equals one session. The `sessionId` field in the JSON is the session ID (fallback: filename stem). Files under `chatSessions/` directories or `emptyWindowChatSessions/` are recognized.

CLI sessions are prioritized over VS Code sessions during discovery (sorted first).

## Format

Mixed: JSONL for CLI events, JSON for VS Code chat sessions, YAML for workspace metadata, SQLite for session store.

### CLI event log format (events.jsonl)

Each line is a JSON object representing an event. The adapter determines role from multiple locations:

Role detection (checked in order):
1. Top-level `role` field
2. `message.role` field
3. `data.role` field (recursive)
4. Inferred from `type`/`event`/`kind` field: tokens containing `prompt`/`user`/`input` map to user; `assistant`/`response`/`output`/`completion` map to assistant

Text content extraction (checked in order): `content`, `text`, `message`, `prompt`, `response`, `delta`, `output`, then recursing into `payload` and `data` objects.

Timestamp extraction (checked in order): `timestamp`, `createdAt`, `updatedAt`, `time`, then recursing into `payload` and `data`.

Tool events are identified by `type`/`event`/`kind` containing the word `"tool"` (also checked recursively in `payload` and `data`). Tool events produce:

- `tool_use` blocks: `toolName`/`name` as tool name, `toolCallId` as ID, `input`/`arguments` as input
- `tool_result` blocks (when kind contains `"result"`): `toolCallId` as ID, content from text extraction, `isError` flag

### VS Code chat session JSON format

```json
{
  "sessionId": "...",
  "creationDate": "...",
  "lastMessageDate": "...",
  "customTitle": "...",
  "computedTitle": "...",
  "version": 1,
  "initialLocation": "...",
  "requests": [
    {
      "requestId": "...",
      "timestamp": "...",
      "message": "..." or {"text": "...", "parts": [...]},
      "response": [...],
      "responseId": "...",
      "modelId": "..."
    }
  ]
}
```

Response array items by `kind`/`type`:
- `toolInvocation`, `toolInvocationSerialized`, `prepareToolInvocation` -- tool call blocks. Fields: `toolName`/`name`, `toolCallId`, `input`/`arguments`/`toolInvocation`
- `textEditGroup`, `notebookEditGroup` -- summarized as "Made changes."
- Items with `content.value` -- text content
- Other items -- text extraction from nested fields

### Workspace YAML format

Simple key-value YAML (parsed line by line). Project path is extracted from keys: `workspace`, `workspace_path`, `root`, `cwd`, `path`.

### Session store database

SQLite database at `session-store.db`. Table names and schemas vary. The adapter looks for tables with a session ID column (`session_id`, `sessionId`, `id`, `session`) and queries for rows matching the current session ID.

### What to ignore

- CLI events with no discernible role and no tool indicators
- Empty text content for user turns
- VS Code response items of kind `toolInvocation`/`toolInvocationSerialized`/`prepareToolInvocation` are extracted as tool blocks, not text

### Metadata

CLI sessions: `workspace_yaml_path`, `workspace_context` (parsed YAML), `session_store` (data from session-store.db including table names and row data).

VS Code sessions: `title` (from `customTitle`/`computedTitle`), `version`, `initialLocation`, `lastMessageDate`.

Per turn: `source_event_type`, `model` (CLI), `requestId`, `responseId`, `modelId` (VS Code).

## What sessions contain

CLI sessions record terminal-based conversations: user prompts, assistant responses, tool calls (shell commands, file operations), tool results, and workspace context. VS Code sessions record editor-based chat: user questions, assistant responses with code suggestions, tool invocations for code editing, and notebook operations.

## Harness memory

GitHub Copilot reads context from these sources:

- `.github/copilot-instructions.md` in the project root (project-level instructions)
- `.github/agents/` directory for custom agent definitions (`.agent.md` files)
- `.github/skills/` directory for agent skills (each with a SKILL.md)
- `AGENTS.md` in the project root (read in some configurations)
- User-level settings configured through the VS Code settings UI or GitHub settings

## Distribution

To write context back to Copilot:

- Write to `.github/copilot-instructions.md` in the project root (markdown format, project instructions)
- Write `.agent.md` files to `.github/agents/` for custom agent definitions
- Write skills to `.github/skills/` for agent skills
- Write to `AGENTS.md` in the project root (supported in some configurations)
