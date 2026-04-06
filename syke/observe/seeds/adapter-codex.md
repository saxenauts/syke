# codex

Codex is OpenAI's CLI agent for code. It runs in a terminal, accepts natural-language prompts, and executes tool calls including shell commands, file operations, web searches, and computer-use actions. Sessions are stored as JSONL rollout files. A SQLite state database tracks session metadata and thread state. Sessions can fork from other sessions and spawn subagent threads.

## Where

```
~/.codex/**/*.jsonl
~/.codex/**/*.db
~/.codex/**/*.sqlite
~/.codex/config.toml
```

Session data lives under two directory names within `~/.codex`:

- `sessions/` -- active session rollout files (JSONL)
- `archived_sessions/` -- archived session rollout files (JSONL)

Additional data sources:

- `session_index.jsonl` -- index file mapping session IDs to metadata
- `state.sqlite` or `state_N.sqlite` (regex: `^state(?:_\d+)?\.sqlite$`) -- SQLite database with thread metadata
- `config.toml` -- may contain a `sqlite_home` key pointing to an alternate SQLite directory
- The `CODEX_SQLITE_HOME` environment variable can override the SQLite location

The adapter also checks `~/.codex/sqlite/` as a default SQLite home.

## Sessions

One JSONL file equals one session (called a "rollout"). The session ID is extracted from the filename: the adapter looks for a UUID-like pattern (`[0-9a-f]{8,}` with hyphen-separated groups) at the end of the stem.

Sessions can have parent-child relationships:

- `forked_from_id` in session metadata indicates a fork
- `source.subagent.thread_spawn.parent_thread_id` indicates a subagent

The `session_index.jsonl` file provides a secondary index with fields `id`, `thread_name`, `updated_at`.

The SQLite state database `threads` table provides richer metadata per session.

## Format

Mixed: JSONL for conversation data, SQLite for metadata, TOML for configuration.

### JSONL rollout files

Each line is a JSON object. Records use a wrapper structure:

```
{"type": "<wrapper_type>", "payload": {...}, "timestamp": "..."}
```

If no `payload` key exists, the record itself may serve as the payload. Records with `record_type == "state"` are skipped.

A record with a top-level `id` and `timestamp` but no `type`/`payload` wrapper is treated as `session_meta`.

### Message types / Turn structure

Payload `type` values that produce turns:

**Content messages** (`message`, `reasoning`):
- `role`: `"user"` or `"assistant"`
- Text content from `payload.text` or from `payload.content[]` blocks of type `input_text`, `output_text`, `summary_text`, `text`
- `reasoning` type messages are prefixed with `[reasoning]`

**Tool call types** (`function_call`, `custom_tool_call`, `web_search_call`, `computer_call`):
- `name` or type name used as tool name
- `call_id` as tool ID (fallback: `{session_id}:tool:{line_index}`)
- `arguments` or `input` as tool input
- `status` field if present

**Tool result types** (`function_call_output`, `custom_tool_call_output`, `web_search_call_output`, `computer_call_output`):
- `call_id` to match back to tool call
- `output` contains result content
- `status` field; non-success statuses (`completed`, `succeeded`, `success`) mark errors
- Output may be JSON string with `metadata.exit_code`

### What to ignore

- Records with `record_type == "state"`
- `session_meta` records (used for metadata extraction only, not conversation turns)
- Records where the unwrap produces no recognizable type
- Empty text content

### SQLite state database (`threads` table)

The `threads` table schema (columns may vary by version):

| Column | Description |
|---|---|
| `id` | Session/thread ID |
| `rollout_path` | Filesystem path to the JSONL rollout file |
| `created_at` | Creation timestamp |
| `updated_at` | Last update timestamp |
| `source` | Session source descriptor |
| `agent_nickname` | Agent display name |
| `agent_role` | Agent role |
| `agent_path` | Agent configuration path |
| `model_provider` | Model provider name |
| `model` | Model identifier |
| `reasoning_effort` | Reasoning effort setting |
| `cwd` | Working directory |
| `cli_version` | CLI version string |
| `title` | Session title |
| `sandbox_policy` | Sandbox policy |
| `approval_mode` | Approval mode |
| `tokens_used` | Total tokens used (integer) |
| `first_user_message` | First user message text |
| `archived_at` | Archive timestamp |
| `git_sha` | Git commit SHA |
| `git_branch` | Git branch |
| `git_origin_url` | Git remote origin URL |

Query example:
```sql
SELECT id, rollout_path, cwd, model, title, created_at, updated_at
FROM threads
ORDER BY updated_at DESC;
```

### Metadata

Per-session metadata from the session meta record and state DB:

- `cli_version`, `originator`, `model_provider` from session meta
- `base_instructions`, `git` (object), `forked_from_id` from session meta
- `thread_name`, `indexed_updated_at` from session index
- All `threads` table columns listed above from state DB
- `source` object containing subagent spawn information

## What sessions contain

Each session records a multi-turn conversation between the user and Codex. This includes: user prompts, assistant text responses and reasoning traces, function calls (shell commands, file operations), web searches, computer-use actions, tool outputs and errors, and session forking/subagent relationships. Archived sessions retain the same format.

## Harness memory

Codex reads context from these sources:

- `AGENTS.md` in the project root (project-level instructions, injected into system prompt)
- `AGENTS.override.md` in the project root or `~/.codex/` (takes priority over AGENTS.md)
- `~/.codex/config.toml` (global configuration)
- `.codex/config.toml` (project-level config override)

## Distribution

To write context back to Codex:

- Write to `AGENTS.md` in the project root (markdown format, injected into system prompt)
- Write to `AGENTS.override.md` in the project root or `~/.codex/` to override AGENTS.md
- Edit `~/.codex/config.toml` for global configuration
- Edit `.codex/config.toml` for project-level config override
