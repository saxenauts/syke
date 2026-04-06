# gemini-cli

Gemini CLI is Google's command-line AI agent. It runs in the terminal, accepts natural-language prompts, and executes tool calls for code editing, shell commands, file operations, and code execution. Session data is stored as JSON files in two categories: chat files (full conversation records) and checkpoint files (conversation snapshots at specific points).

## Where

```
~/.gemini/tmp/**/chats/**/*.json
~/.gemini/tmp/**/checkpoints/**/*.json
```

The directory structure is:
```
~/.gemini/tmp/<project_hash>/chats/<session_id>.json
~/.gemini/tmp/<project_hash>/chats/<parent_session_id>/<child_session_id>.json
~/.gemini/tmp/<project_hash>/checkpoints/<checkpoint_name>.json
```

The `<project_hash>` is a hash identifying the project. It is extracted from the path component immediately after `tmp/`.

Chat files are prioritized over checkpoint files during discovery.

A file is recognized as a chat file if its path contains `chats` after the `tmp/<project_hash>/` prefix. A file is recognized as a checkpoint file if its path contains `checkpoints` after the same prefix. Both must have a `.json` extension.

## Sessions

**Chat files**: One JSON file equals one session. The session ID comes from the `sessionId` field in the JSON, or from the filename stem (with `session-` prefix removed for top-level chats).

Subagent sessions are identified by directory depth under `chats/`: if the file is nested more than one level deep (e.g., `chats/<parent_id>/<child_id>.json`), the intermediate directory name is the parent session ID. The `kind` field set to `"subagent"` also indicates a subagent.

**Checkpoint files**: One JSON file equals one session snapshot. The session ID is formatted as `<project_hash>:checkpoint:<base_id>` where base_id comes from `sessionId` in the JSON or the filename stem.

Checkpoints can appear in two formats:
1. Conversation format (has a `messages` list) -- parsed identically to chat files
2. State format (has `clientHistory`, `history`, or `toolCall`) -- parsed from Gemini API structures

Deduplication: if the same session ID appears from multiple files, only the first is kept.

## Format

JSON. Each file is a single JSON object.

### Chat file format

```json
{
  "sessionId": "...",
  "startTime": "...",
  "lastUpdated": "...",
  "summary": "...",
  "kind": "subagent",
  "directories": ["..."],
  "messages": [
    {
      "type": "user",
      "content": [...],
      "timestamp": "..."
    },
    {
      "type": "gemini",
      "content": [...],
      "displayContent": [...],
      "thoughts": [...],
      "toolCalls": [...],
      "model": "...",
      "tokens": {"inputTokens": ..., "outputTokens": ...},
      "timestamp": "..."
    }
  ]
}
```

### Message types / Turn structure

Messages have a `type` field:

**User messages** (`type == "user"`):
- `content` is a list of parts. Text parts: `{"text": "..."}`. Also supports inline data, file data, executable code, and code execution results.

**Assistant messages** (`type == "gemini"`):
- `displayContent` takes priority over `content` for text extraction
- `thoughts` is a list of thought objects: `{"subject": "...", "description": "..."}` -- formatted as `[thoughts]` block
- `toolCalls` is a list of tool call objects:

```json
{
  "id": "...",
  "name": "tool_name",
  "args": {"key": "value"},
  "status": "success",
  "result": [...]
}
```

Each tool call produces a `tool_use` block. If `result` is present, a `tool_result` block is also produced. Error detection: `status` not in `{success, succeeded, ok, completed}`.

- `tokens` object contains token usage
- `model` field identifies the model used

### Content parts structure (used in content, displayContent, and result fields)

Parts are a list (or a single value). Each part can be:

| Part type | Fields | Description |
|---|---|---|
| Text | `{"text": "..."}` | Plain text |
| Function call | `{"functionCall": {"id": "...", "name": "...", "args": {...}}}` | Tool invocation |
| Function response | `{"functionResponse": {"id": "...", "name": "...", "response": {...}}}` | Tool result. Error if `response.success == false`, `response.exit_code != 0`, or `response.error` present |
| Executable code | `{"executableCode": {"code": "..."}}` | Code block (formatted as `[code]`) |
| Code execution result | `{"codeExecutionResult": {"output": "..."}}` | Execution output (formatted as `[execution_result]`) |
| Inline data | `{"inlineData": ...}` | Binary data (formatted as `[inline_data]`) |
| File data | `{"fileData": ...}` | File reference (formatted as `[file_data]`) |

### Checkpoint state format

Checkpoints without a `messages` list are parsed from these fields:

1. `clientHistory` -- Gemini API format. List of `{role, parts}` objects. `role` is `"user"` or `"model"`/`"assistant"`. Parts use the same content parts structure above.

2. `history` -- UI history format. List of objects with `type` field:
   - `"user"` -- user text (from `text` or `content` field)
   - `"gemini"` or `"assistant"` -- assistant text
   - `"tool_group"` -- contains `tools` list with `{callId, name, args, status, result}` objects

3. `toolCall` -- a single pending tool call object (`{name, args}`)

Additional checkpoint fields: `messageId`, `commitHash`, `timestamp`.

### What to ignore

- Files not under a `tmp/<hash>/chats/` or `tmp/<hash>/checkpoints/` path structure
- Messages with types other than `"user"` or `"gemini"` in chat files
- Assistant messages with no text content and no tool calls
- `inlineData` and `fileData` parts (recorded as placeholders only)
- Empty user messages

### Metadata

Per session: `project_hash`, `summary`, `kind`, `directories` (list of project directories).

Per checkpoint session: `checkpoint_name`, `checkpoint_format` (`"conversation"` or state-based), `message_id`, `commit_hash`, `has_client_history`, `has_history`.

Per assistant turn: `model`, `tokens` (usage object).

## What sessions contain

Chat sessions record multi-turn conversations between the user and the Gemini CLI agent within a project. This includes: user prompts, assistant text responses and thoughts, tool calls (function calls, code execution, file operations), tool results, and project directory context. Checkpoint files capture the conversation state at specific points, potentially including uncommitted tool call state.

## Harness memory

Gemini CLI reads context from these sources:

- `GEMINI.md` in the project root (project-level instructions, injected into context)
- `~/.gemini/GEMINI.md` (user-level global context)
- Subdirectory `GEMINI.md` files for component-level context (scoped to that subtree)
- `.gemini/settings.json` for project-specific configuration
- `~/.gemini/settings.json` for global settings
- The context filename (`GEMINI.md` by default) is configurable in settings

## Distribution

To write context back to Gemini CLI:

- Write to `GEMINI.md` in the project root (markdown format, project instructions injected into context)
- Write to `~/.gemini/GEMINI.md` for global context across all projects
- Write `GEMINI.md` files in subdirectories for component-level scoped context
- Edit `.gemini/settings.json` for project-specific configuration
- Edit `~/.gemini/settings.json` for global configuration
