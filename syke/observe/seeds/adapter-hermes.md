# hermes

Hermes is a terminal-based AI coding agent. It runs as a CLI tool, accepts natural-language prompts, and executes tool calls for code editing, shell commands, and file operations. Session data is stored in a SQLite database and optionally as JSON session files and request dump files. Sessions can have parent-child relationships.

## Where

```
~/.hermes/state.db
~/.hermes/sessions/*.json
```

The adapter discovers three file types:

- `state.db` -- SQLite database (primary, preferred over JSON files)
- `session_YYYYMMDD_HHMMSS_<hex>.json` -- session JSON export files
- `request_dump_YYYYMMDD_HHMMSS_<hex>_*.json` -- raw API request dumps

Session filename patterns (regex):
- Session files: `^session_(?P<session_id>\d{8}_\d{6}_[0-9a-f]+)\.json$`
- Request dumps: `^request_dump_(?P<session_id>\d{8}_\d{6}_[0-9a-f]+)_.+\.json$`

When both `state.db` and JSON files exist, the database is preferred. JSON files serve as fallback or supplementary data.

## Sessions

**From SQLite**: Multiple sessions live in the `sessions` table. Each row is one session identified by `id`. Messages for each session are in the `messages` table joined by `session_id`.

**From session JSON files**: One file equals one session. The `session_id` field in the JSON is the session ID (fallback: extracted from the filename pattern).

**From request dump files**: One file equals one session snapshot. Contains the raw API request body including the message history at that point in time. The `session_id` field or filename pattern provides the ID.

Parent-child relationships are tracked via `parent_session_id` in the sessions table.

Deduplication: if the same session ID is encountered from multiple sources, only the first occurrence is kept (database results are processed first).

## Format

Mixed: SQLite (primary), JSON (secondary).

### SQLite schema

#### Table: `sessions`

| Column | Type | Description |
|---|---|---|
| `id` | text | Session identifier |
| `source` | text | Session source descriptor |
| `user_id` | text | User identifier |
| `model` | text | Model identifier |
| `model_config` | text | JSON blob of model configuration |
| `system_prompt` | text | System prompt used |
| `parent_session_id` | text | Parent session ID (null for root sessions) |
| `started_at` | real | Start timestamp (epoch seconds) |
| `ended_at` | real | End timestamp (epoch seconds) |
| `end_reason` | text | Why the session ended |
| `message_count` | integer | Total message count |
| `tool_call_count` | integer | Total tool call count |
| `input_tokens` | integer | Total input tokens |
| `output_tokens` | integer | Total output tokens |
| `title` | text | Session title |
| `cache_read_tokens` | integer | Cache read tokens |
| `cache_write_tokens` | integer | Cache write tokens |
| `reasoning_tokens` | integer | Reasoning tokens |
| `billing_provider` | text | Billing provider name |
| `billing_base_url` | text | Billing API base URL |
| `billing_mode` | text | Billing mode |
| `estimated_cost_usd` | real | Estimated cost in USD |
| `actual_cost_usd` | real | Actual cost in USD |
| `cost_status` | text | Cost tracking status |
| `cost_source` | text | Cost data source |
| `pricing_version` | text | Pricing version used |

#### Table: `messages`

| Column | Type | Description |
|---|---|---|
| `id` | integer | Message ID (auto-increment) |
| `session_id` | text | Foreign key to `sessions` |
| `role` | text | `"user"`, `"assistant"`, or `"tool"` |
| `content` | text | Message content (text or JSON string) |
| `tool_call_id` | text | Tool call ID this message responds to (for `role == "tool"`) |
| `tool_calls` | text | JSON array of tool calls (for `role == "assistant"`) |
| `tool_name` | text | Tool name (for `role == "tool"`) |
| `timestamp` | real | Message timestamp (epoch seconds) |
| `token_count` | integer | Token count for this message |
| `finish_reason` | text | Generation finish reason |
| `reasoning` | text | Reasoning text |
| `reasoning_details` | text | JSON blob of detailed reasoning |
| `codex_reasoning_items` | text | JSON blob of Codex-format reasoning items |

Messages are ordered by `timestamp ASC, id ASC`.

### Message types / Turn structure

**User turns** (`role == "user"`): `content` is plain text.

**Assistant turns** (`role == "assistant"`):
- `content` is the assistant's text response
- `reasoning` is prefixed with `[reasoning]`
- `reasoning_details` (JSON list) is prefixed with `[reasoning_details]`
- `codex_reasoning_items` (JSON list) is prefixed with `[codex_reasoning]`
- `tool_calls` is a JSON array of tool call objects:

```json
[
  {
    "id": "call_abc",
    "type": "function",
    "function": {
      "name": "tool_name",
      "arguments": "{\"key\": \"value\"}"
    }
  }
]
```

The `function.arguments` field is a JSON string that gets parsed. `id` or `call_id` serves as the tool ID.

**Tool result turns** (`role == "tool"`):
- `tool_call_id` matches back to the tool call
- `tool_name` identifies which tool produced the result
- `content` is the result (may be a JSON string). If parseable as JSON, the adapter checks for `output` (string) within it. Error detection: `exit_code != 0`, `error` field present, `success == false`.

### Session JSON format

```json
{
  "session_id": "...",
  "session_start": "...",
  "last_updated": "...",
  "model": "...",
  "base_url": "...",
  "platform": "...",
  "message_count": 42,
  "tools": [...],
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...], "reasoning": "..."},
    {"role": "tool", "tool_call_id": "...", "content": "..."}
  ]
}
```

### Request dump JSON format

```json
{
  "session_id": "...",
  "timestamp": "...",
  "reason": "...",
  "error": "...",
  "request": {
    "url": "...",
    "method": "POST",
    "body": {
      "model": "...",
      "messages": [...],
      "tools": [...]
    }
  }
}
```

Messages within request dumps follow the same structure as session JSON messages. System messages (`role == "system"`) are skipped.

### What to ignore

- System messages in request dumps
- Empty content for user turns
- Assistant turns with no content and no tool calls
- Records where role is not `user`, `assistant`, or `tool`

### Metadata

Per session (DB): `model`, `title`, `end_reason`, `billing_provider`, `billing_base_url`, `billing_mode`, `cost_status`, `cost_source`, `pricing_version`, `message_count`, `tool_call_count`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, `estimated_cost_usd`, `actual_cost_usd`, `model_config`.

Per session (JSON): `model`, `base_url`, `platform`, `message_count`, `tools_count`.

Per session (request dump): `reason`, `error`, `request_url`, `request_method`, `model`, `tools_count`.

Per turn: `source_message_id`, `finish_reason`, `token_count`.

Query example to list recent sessions:
```sql
SELECT id, title, model, started_at, ended_at, message_count,
       input_tokens, output_tokens, estimated_cost_usd
FROM sessions
ORDER BY started_at DESC
LIMIT 20;
```

## What sessions contain

Each session records a multi-turn conversation between the user and the Hermes agent. This includes: user prompts, assistant text responses and reasoning traces (including detailed reasoning and Codex-format reasoning), tool calls with function names and arguments, tool results with output and error states, and session-level cost and token accounting.

## Harness memory

Hermes reads context and configuration from these sources:

- `~/.hermes/config.yaml` (main configuration)
- `~/.hermes/memories/` (persistent memory directory)
- `~/.hermes/skills/` (skills directory)
- `~/.hermes/hooks/` (hooks for automated behaviors)
- `AGENTS.md` in the project root (project instructions)
- Profile support with isolated `HERMES_HOME` directories for separate configurations

## Distribution

To write context back to Hermes:

- Write to `AGENTS.md` in the project root (markdown format, project instructions)
- Edit `~/.hermes/config.yaml` for main configuration
- Write to `~/.hermes/memories/` for persistent context across sessions
- Write to `~/.hermes/skills/` for skill definitions
