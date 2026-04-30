# cursor

Cursor is an AI-powered code editor built on VS Code. It provides chat and composer interfaces where the user types prompts and an AI assistant responds with text, code suggestions, and tool calls. Session data is stored in two formats: VS Code state databases (SQLite with `.vscdb` extension) and JSON chat session files.

## Where

macOS:
```
~/Library/Application Support/Cursor/User/workspaceStorage/**/chatSessions/*.json
~/Library/Application Support/Cursor/User/workspaceStorage/**/chatSessions/*.jsonl
~/Library/Application Support/Cursor/User/workspaceStorage/**/state.vscdb
~/Library/Application Support/Cursor/User/workspaceStorage/**/state.vscdb_backup
~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
~/Library/Application Support/Cursor/User/globalStorage/state.vscdb_backup
```

Linux:
```
~/.config/Cursor/User/workspaceStorage/**/chatSessions/*.json
~/.config/Cursor/User/workspaceStorage/**/chatSessions/*.jsonl
~/.config/Cursor/User/workspaceStorage/**/state.vscdb
~/.config/Cursor/User/workspaceStorage/**/state.vscdb_backup
~/.config/Cursor/User/globalStorage/state.vscdb
~/.config/Cursor/User/globalStorage/state.vscdb_backup
```

The adapter discovers `.vscdb` files and JSON/JSONL files under `chatSessions/` directories. State databases are prioritized over JSON files during discovery.

## Sessions

Sessions come from two sources with different scoping:

1. **State databases** (`state.vscdb`): A key-value SQLite database. The adapter scans all tables for keys matching the regex `(composerData|chat|conversation|session)` (case-insensitive). The value is a JSON blob containing one session's messages. One matching key-value pair produces one session.

2. **Chat session JSON files** (under `chatSessions/`): One JSON file equals one session. Files contain a `requests` array of request/response pairs.

Session IDs come from (in priority order): `sessionId`, `composerId`, `conversationId`, `id` fields in the payload, or the filename stem.

Workspace association is determined by the `workspace.json` file in parent directories (the `folder` field with `file://` prefix stripped, or the `workspace` field).

## Format

Mixed: SQLite (`.vscdb`) for state databases, JSON for chat session files.

### State database format (state.vscdb)

SQLite database with a key-value table structure. The table name varies. The adapter auto-detects tables and looks for:

- Key column: named `key`, `itemKey`, or `name`
- Value column: named `value`, `itemValue`, `data`, `json`, or `blob`

Values are JSON strings (possibly UTF-16 encoded byte blobs) containing conversation data.

The JSON blob structure varies but the adapter extracts messages from these paths:
- `payload.messages` (list)
- `payload.conversation` (list or object with `.messages`)
- `payload.chat` (list or object with `.messages`)
- `payload.tabs[].messages`, `payload.entries[].messages`, `payload.sessions[].messages`

### Chat session JSON format

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
      "message": {"text": "...", "parts": [...]},
      "response": [...],
      "responseId": "...",
      "modelId": "..."
    }
  ]
}
```

### Message types / Turn structure

**From state databases**, messages are individual objects in a list:

| Field | Description |
|---|---|
| `role` / `type` / `author` | Role: `"user"`, `"human"`, `"assistant"`, `"model"`, `"ai"` |
| `text` / `content` / `message` / `body` | Text content |
| `timestamp` / `createdAt` / `updatedAt` / `time` | Timestamp |
| `toolCalls` / `tools` | List of tool call objects |

Tool calls within messages:

| Field | Description |
|---|---|
| `id` | Tool call ID |
| `name` | Tool name |
| `args` / `input` | Tool input |
| `result` | Tool result (if present, produces a tool_result block) |

**From chat session JSON files**, turns come in request/response pairs:

- User turn: extracted from `request.message` (string or object with `text` and `parts`)
- Assistant turn: extracted from `request.response` (list of content items)

Response items by `kind`/`type`:
- `toolInvocation`, `toolInvocationSerialized`, `prepareToolInvocation` -- tool calls (extracted as tool_use blocks with `toolName`, `toolCallId`, `input`/`arguments`)
- `textEditGroup`, `notebookEditGroup` -- summarized as "Made changes."
- Items with `content.value` -- text content
- Other items -- general text extraction

### What to ignore

- State database keys that do not match the regex `(composerData|chat|conversation|session)`
- Response items with kind `toolInvocation`/`toolInvocationSerialized`/`prepareToolInvocation` are extracted as tool blocks, not text
- Empty user messages

### Metadata

Per session from state DB: `state_table`, `state_key` identifying where in the database the session was found.

Per session from JSON: `title` (from `customTitle` or `computedTitle`), `version`, `initialLocation`, `lastMessageDate`.

Per turn from JSON: `requestId`, `responseId`, `modelId`.

## What sessions contain

Each session records a conversation between the user and the Cursor AI within a workspace. This includes: user prompts, assistant text responses, tool invocations (code edits, file reads, terminal commands), tool results, and workspace context. The data captures both chat-mode interactions and composer-mode multi-file editing sessions.

## Harness memory

Cursor reads project-level AI instructions from these sources:

- `.cursor/rules/` directory (preferred, MDC format with `.mdc` extension). Rule files use frontmatter metadata for path-based scoping (e.g., `globs`, `description`, `alwaysApply`)
- `.cursorrules` in the project root (deprecated in favor of `.cursor/rules/`)
- `.cursor/settings.json` for project-specific settings

## Distribution

To write context back to Cursor:

- Write `.mdc` files to `.cursor/rules/` (MDC format with YAML frontmatter for path-based scoping, not plain markdown)
- Write to `.cursorrules` in the project root (plain text, deprecated but still supported)
- Edit `.cursor/settings.json` for project-specific settings

Rule files in `.cursor/rules/` are injected into the system prompt for AI interactions within that project, scoped by the frontmatter metadata.
