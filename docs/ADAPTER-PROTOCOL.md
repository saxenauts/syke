# Adapter Protocol — The Adapter Droid's Manual

> Specification for AI agents generating, validating, and healing Syke Observe adapters. This is the contract. Follow it precisely.

---

## §1 Purpose

Adapters are compilers. They transform harness-native formats into Syke's canonical event intermediate representation (IR). An adapter doesn't interpret meaning. It doesn't classify content. It doesn't summarize. It parses mechanically and emits typed events.

This document is the contract AI agents follow to:
- **Generate** new adapters from harness format documentation
- **Validate** that adapters conform to the Observe layer principles
- **Heal** adapters when harness formats change or ingestion fails

The protocol is unambiguous. If two competent implementers could disagree about output for the same input, the specification is insufficient. There is no ambiguity here.

---

## §2 The Compilation Target

Every adapter produces **ObservedSession** and **ObservedTurn** objects. The base class converts these to canonical events. See [OBSERVE-SCHEMA.md](OBSERVE-SCHEMA.md) for the complete storage schema.

**The events table is the IR:**

```sql
events (
  -- Universal (always present)
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  content TEXT NOT NULL,
  user_id TEXT NOT NULL,
  external_id TEXT,
  ingested_at TEXT,
  title TEXT,

  -- Grouping hints (nullable when harness doesn't provide)
  session_id TEXT,
  parent_session_id TEXT,

  -- Ordering
  sequence_index INTEGER,

  -- Typed known fields from harness
  role TEXT,
  model TEXT,
  stop_reason TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cache_read_tokens INTEGER,
  cache_creation_tokens INTEGER,
  tool_name TEXT,
  tool_correlation_id TEXT,
  is_error INTEGER DEFAULT 0,
  duration_ms INTEGER,

  -- Causality
  parent_event_id TEXT,

  -- Provenance
  source_event_type TEXT,
  source_path TEXT,
  source_line_index INTEGER,

  -- Narrow escape hatch
  extras TEXT DEFAULT '{}'
)
```

**Event type taxonomy:**

| event_type | Purpose | Content |
|------------|---------|---------|
| `session.start` | Session envelope | Metadata summary (project, duration, turn counts) |
| `turn` | User or assistant message | Message text (text + thinking blocks only) |
| `tool_call` | Tool invocation | Full input JSON |
| `tool_result` | Tool output | Full output text |
| `ingest.error` | Parse/filter failure | Error description with provenance |

**Key invariant:** The adapter populates `ObservedSession` and `ObservedTurn`. The base class handles conversion to `Event` objects. Never write SQL in an adapter. Never import `Event` directly.

---

## Vocabulary

Three names, used consistently:

**Observe Adapter**: Deterministic compiler inside Syke. Python class, ObserveAdapter subclass.
**Connector Skill**: Installed `syke-observe-<harness>/SKILL.md` in the harness. Inbound configuration.
**Context Skill**: Installed `syke-context/SKILL.md` in the harness. Outbound memory distribution.

The **adapter-droid** maintainer skill manages the connector skill + adapter/descriptor pair: create, health-check, heal, verify.

---

## §3 The ObserveAdapter Contract

All adapters inherit from `ObserveAdapter` and implement exactly two methods. See `syke/ingestion/observe.py` for the base class.

### Method 1: `discover() -> list[Path]`

**Purpose:** Find all harness data artifacts on disk.

**Contract:**
- Returns a list of `Path` objects pointing to files or directories
- Must be deterministic — same disk state = same paths
- Must handle missing directories gracefully (return empty list)
- Must follow symlinks when harness uses them
- Must not ingest partial files (check file completeness)

**Example implementations:**
- JSONL harness: Return list of `.jsonl` files
- SQLite harness: Return list of `.db` or `.vscdb` files
- Multi-file harness: Return list of task directories

### Method 2: `iter_sessions(since: float = 0) -> Iterable[ObservedSession]`

**Purpose:** Yield parsed sessions from discovered artifacts.

**Contract:**
- Yields `ObservedSession` objects, not raw events
- Respects `since` parameter — skip sessions with `start_time` before timestamp
- Parses files identified by `discover()`
- Handles parse errors by logging and continuing (Principle 7)
- Preserves source order within each session

**ObservedSession fields:**

```python
@dataclass
class ObservedSession:
    session_id: str              # Harness-native session identifier
    source_path: Path            # Path to source artifact
    start_time: datetime         # Session start timestamp
    end_time: datetime | None    # Session end (nullable)
    project: str | None          # Project/directory context
    parent_session_id: str | None # Parent session (subagent)
    turns: list[ObservedTurn]    # Ordered list of turns
    metadata: dict[str, Any]     # Harness-specific extras
    is_subagent: bool = False    # Subagent flag
    agent_id: str | None = None  # Subagent identifier
    agent_slug: str | None = None # Subagent slug
```

**ObservedTurn fields:**

```python
@dataclass
class ObservedTurn:
    role: str                    # "user" | "assistant" | harness-native
    content: str                 # Full text content
    timestamp: datetime          # Turn timestamp
    uuid: str | None = None      # Harness-native turn UUID
    parent_uuid: str | None = None # Parent turn UUID
    tool_calls: list[dict]       # Tool use/result blocks
    metadata: dict[str, Any]     # Usage, model, stop_reason, etc.
```

**Critical rule:** `iter_sessions` does NOT write to the database. It yields data. The base class `ingest()` method handles database insertion with proper transactions and idempotency.

---

## §3.1 Transport Declarations

Each harness descriptor may declare which transport modes it supports. These are declared in the TOML descriptor and used by the observe-rt service to select the highest-fidelity capture path.

```toml
[transport]
hook = { type = "http", events = ["PostToolUse", "Stop", "SessionStart"] }
watch = { paths = ["~/.claude/projects"], patterns = ["**/*.jsonl"] }
native = { type = "sse", endpoint = "/sse" }
poll = { interval_minutes = 15 }
```

Transport priority: hook > native > watch > poll. The observe-rt service selects the highest available tier. Poll is always the safety net.

---

## §4 Descriptor Format

Descriptor-driven adapters use TOML configuration instead of custom Python. The descriptor declares how to discover, parse, and transform harness data.

### Minimal Stub Descriptor (5 lines)

```toml
[adapter]
name = "example"
source = "example-harness"
version = "1.0.0"

[discover]
pattern = "~/.example/sessions/*.jsonl"
```

### Full Descriptor Schema

```toml
[adapter]
name = "claude-code"           # Adapter identifier
source = "claude-code"         # Source field in events table
version = "1.0.0"              # Descriptor version for regeneration
description = "Claude Code JSONL sessions"

[discover]
pattern = "~/.claude/projects/*/sessions/*.jsonl"  # Glob pattern
type = "jsonl"                 # File type: jsonl | json | sqlite | multifile | markdown
recursive = true               # Search subdirectories
follow_symlinks = true         # Follow symlinks

[session]
id_field = "session_id"      # Field or template for session ID
timestamp_field = "timestamp"  # Field for session timestamp
project_field = "project"      # Field for project context
parent_session_field = "parent_session_id"  # Nullable

[session.from_path]            # Extract from filename/path
id_pattern = "session-([a-z0-9]+)\\.jsonl"
timestamp_fallback = "mtime"   # Use file mtime if no field

[turn]
role_field = "type"            # Field containing role
role_mapping = { user = "user", assistant = "assistant" }
content_field = "message.content"  # Dot-notation path
content_type = "text"          # text | blocks | markdown
timestamp_field = "timestamp"
sequence_from = "line_index"   # Derive sequence from file position

[turn.blocks]                  # For block-based content (Claude-style)
text_block_type = "text"
thinking_block_type = "thinking"
tool_use_type = "tool_use"
tool_result_type = "tool_result"
tool_name_field = "name"
tool_id_field = "id"
tool_input_field = "input"

[turn.usage]                   # Token usage extraction
input_tokens_field = "usage.input_tokens"
output_tokens_field = "usage.output_tokens"
cache_read_tokens_field = "usage.cache_read_input_tokens"
cache_creation_tokens_field = "usage.cache_creation_input_tokens"

[metadata]
model_field = "message.metadata.model"
stop_reason_field = "message.stop_reason"
source_event_type_field = "type"
git_branch_field = "message.metadata.git_branch"
cwd_field = "message.metadata.cwd"

[external_id]
template = "{source}:{session_id}:turn:{turn_index}"  # Stable ID template
```

### [external_id] Section — Required

External IDs must be **deterministic and stable**. See §7 for full rules.

```toml
[external_id]
session_template = "{source}:{session_id}:start"
turn_template = "{source}:{session_id}:turn:{turn_index}"
tool_call_template = "{source}:{session_id}:tool_call:{turn_index}:{tool_index}"
tool_result_template = "{source}:{session_id}:tool_result:{turn_index}:{tool_index}"
```

**Template variables:**
- `{source}` — adapter source name
- `{session_id}` — session identifier
- `{turn_index}` — zero-based turn index in session
- `{tool_index}` — zero-based tool index in turn
- `{line_number}` — source file line number
- `{tool_id}` — harness-native tool correlation ID

---

## §4.1 SKILL.md Package Contracts

Each harness integration has two SKILL.md packages per the Agentic AI Foundation standard:

**`syke-context/SKILL.md`** (outbound — installed in harness): Provides memory context to the agent. Contains: memex injection instructions, `syke ask` and `syke record` command references. This is the outbound half of the bidirectional loop.

**`syke-observe-<harness>/SKILL.md`** (inbound — installed in harness): Configures the observation connection. Contains: transport setup instructions (which hooks to enable, which directories to watch), health check commands, heal/repair recipes.

---

## §5 Format Clusters

Harnesses cluster into 6 format families. Each cluster shares discovery, parsing, and transformation patterns.

### Cluster 1: JSONL (Append-Only Line-Delimited)

**Characteristics:** One JSON object per line, append-only logs, line order = event order.

**Examples:** Claude Code, Codex, OpenClaw, Pi

**Data paths:**
- Claude Code: `~/.claude/projects/*/sessions/*.jsonl`
- Codex: `~/.codex/sessions/**/*.jsonl`
- OpenClaw: `~/.openclaw/agents/*/sessions/*.jsonl`

**Line structure (Claude Code):**
```json
{"type": "user", "timestamp": "2025-03-14T10:00:00Z", "message": {"content": "Hello"}}
{"type": "assistant", "timestamp": "2025-03-14T10:00:05Z", "message": {"content": [{"type": "text", "text": "Hi there"}]}}
```

**Line structure (Codex):**
```json
{"type": "response_item", "timestamp": 1710420000, "payload": {"role": "assistant", "content": "..."}}
```

**Descriptor pattern:**
```toml
[discover]
pattern = "~/.claude/projects/*/sessions/*.jsonl"
type = "jsonl"

[turn]
role_field = "type"
content_field = "message.content"
timestamp_field = "timestamp"
```

### Cluster 2: JSON (Single File or Export)

**Characteristics:** One JSON file per session, entire conversation in a structured object.

**Examples:** Gemini CLI, Continue.dev, ChatGPT export

**Data paths:**
- Gemini CLI: `~/.gemini/tmp/<slug>/chats/session-*.json`
- Continue.dev: `~/.continue/sessions/<id>.json`
- ChatGPT: ZIP export containing `conversations.json`

**File structure (Continue.dev):**
```json
{
  "sessionId": "abc123",
  "title": "Refactoring auth",
  "history": [
    {"role": "user", "content": "Help me refactor..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

**File structure (ChatGPT export):**
```json
{
  "conversations": [{
    "id": "conv-123",
    "title": "Python help",
    "mapping": {
      "node-1": {"message": {"content": "..."}, "children": ["node-2"]},
      "node-2": {"message": {"content": "..."}, "parent": "node-1"}
    }
  }]
}
```

**Descriptor pattern:**
```toml
[discover]
pattern = "~/.continue/sessions/*.json"
type = "json"

[session]
id_field = "sessionId"
title_field = "title"

[turn]
role_field = "role"
content_field = "content"
array_path = "history"  # Path to turns array
```

### Cluster 3: SQLite (Database with JSON Blobs)

**Characteristics:** SQLite database, conversation data in TEXT/BLOB columns as JSON.

**Examples:** Cursor, OpenCode, Windsurf

**Data paths:**
- Cursor: `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`
- OpenCode: `~/.local/share/opencode/opencode.db` (Linux), `~/Library/Application Support/opencode/` (macOS)

**Table structure (Cursor):**
```sql
-- Table: cursorDiskKV
-- Key: composerData:<bubbleId> (JSON blob)
-- Contains: messages array, title, timestamp
```

**Table structure (OpenCode):**
```sql
-- Tables: session, message, part
-- session: id, created_at, updated_at
-- message: id, session_id, role, created_at
-- part: id, message_id, type, content, metadata (JSON)
```

**Descriptor pattern (Cursor):**
```toml
[discover]
pattern = "~/Library/Application Support/Cursor/User/globalStorage/*.vscdb"
type = "sqlite"

[session]
table = "cursorDiskKV"
key_pattern = "composerData:%"
json_column = "value"
id_path = "bubbleId"
messages_path = "messages"
```

### Cluster 4: Multi-File (Directory Per Task)

**Characteristics:** Each session/task in its own directory, multiple files per session.

**Examples:** Cline, Roo Code

**Data paths:**
- Cline: `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<id>/`
- Roo Code: `~/Library/Application Support/Code/User/globalStorage/RooVetGit.roo-code/tasks/<id>/`

**Directory structure:**
```
tasks/<task-id>/
  ui_messages.json      # Conversation messages
  api_conversation.json # API-level data
  task.json             # Task metadata
```

**Descriptor pattern:**
```toml
[discover]
pattern = "~/Library/Application Support/Code/User/globalStorage/*/tasks/*"
type = "multifile"

[session]
from_directory = true
dir_id_pattern = "tasks/([a-z0-9-]+)"
messages_file = "ui_messages.json"
metadata_file = "task.json"
```

### Cluster 5: Markdown (Freeform Text)

**Characteristics:** Human-readable text with delimiter-based structure, no strict schema.

**Examples:** Aider

**Data path:**
- Aider: `.aider.chat.history.md` in git repository root

**File structure:**
```markdown
#### User request in project root

Help me refactor the auth module

#### Assistant response

I'll help you refactor the auth module. Let me start by examining the current implementation.

> System: File read: src/auth.py

Here are the issues I found...
```

**Parsing strategy:**
- `####` prefix = user message
- Plain text after `#### Assistant response` = assistant message
- `>` prefix = system/tool output

**Descriptor pattern:**
```toml
[discover]
pattern = "**/.aider.chat.history.md"
type = "markdown"

[turn]
user_delimiter = "^#### .*"
assistant_delimiter = "^#### .*[Aa]ssistant.*"
content_until_next = true
```

### Cluster 6: Cloud API (HTTP/REST or Stream)

**Characteristics:** Cloud-primary, no local files, requires API polling or webhook.

**Examples:** Amp, GitHub, Gmail

**Data paths:**
- Amp: Cloud-primary (GCP), NDJSON via `--stream-json` CLI flag
- GitHub: REST API `api.github.com`
- Gmail: Gmail API `gmail.googleapis.com`

**Capture strategies:**
- **API polling:** Periodic HTTP requests with ETag/If-Modified-Since
- **Stream JSON:** NDJSON stream from CLI with `--stream-json`
- **Webhook:** POST endpoint for real-time push

**Descriptor pattern:**
```toml
[discover]
type = "cloud_api"
api_endpoint = "https://api.example.com/v1/sessions"
auth_type = "bearer"  # bearer | basic | oauth2

[pagination]
type = "cursor"  # cursor | offset | link_header
cursor_field = "next_cursor"
```

---

## §6 The Fixed Parser Registry

These functions from `syke/ingestion/parsers.py` are the only parsing primitives. Descriptors reference them by name. No custom parsing logic in descriptors — if a format needs custom logic, generate a Python adapter instead.

### `read_jsonl(fpath: Path) -> list[dict[str, object]]`

**Purpose:** Read line-delimited JSON file.

**Input:** Path to `.jsonl` file.

**Output:** List of parsed JSON objects. Malformed lines are logged and skipped.

**Behavior:**
- Skips empty lines
- Logs warning if all lines fail to parse
- Logs debug count of skipped lines if partial success

### `parse_timestamp(line: dict[str, object]) -> datetime | None`

**Purpose:** Extract and normalize timestamp from JSON object.

**Input:** Dictionary potentially containing timestamp field.

**Output:** `datetime` in UTC, or `None` if unparseable.

**Supported formats:**
- ISO8601 string: `"2025-03-14T10:00:00Z"`
- Unix milliseconds: `1710420000000`
- Unix seconds: `1710420000` (auto-detected)

### `extract_text_content(line: dict[str, object]) -> str`

**Purpose:** Extract human-readable text content from message object.

**Input:** Dictionary with potential content in various structures.

**Output:** Concatenated text string.

**Handles:**
- Simple string content: `"content": "hello"`
- Block arrays: `"content": [{"type": "text", "text": "hello"}]`
- Nested message objects: `"message": {"content": "..."}`
- Thinking blocks: prefixed with `[thinking]`

### `extract_tool_blocks(line: dict[str, object]) -> list[dict[str, object]]`

**Purpose:** Extract tool use and tool result blocks.

**Input:** Dictionary with potential tool blocks in content.

**Output:** List of normalized tool block dictionaries.

**Block types returned:**
```python
# Tool use
{
  "block_type": "tool_use",
  "tool_name": "read_file",
  "tool_id": "toolu_abc123",
  "input": {"path": "src/main.py"}
}

# Tool result
{
  "block_type": "tool_result",
  "tool_use_id": "toolu_abc123",
  "content": "file contents...",
  "is_error": false
}
```

### `decode_project_dir(dirname: str) -> str`

**Purpose:** Decode project directory from hyphen-encoded path segment.

**Input:** Directory name like `"-home-user-projects-myapp"`.

**Output:** Resolved path like `"~/projects/myapp"`.

**Algorithm:** Depth-first search through filesystem, trying hyphen and space segmentations.

### `measure_content(text: str) -> tuple[int, int]`

**Purpose:** Measure content size.

**Input:** Text string.

**Output:** Tuple of `(character_count, estimated_token_count)`.

**Token estimation:** `CHARS_PER_TOKEN_ESTIMATE = 4` (configurable).

---

## §7 The external_id Contract

External ID stability is **sacred**. Changing it breaks idempotency. Re-ingesting the same artifact must produce zero new events. This only works if external IDs are deterministic.

### The Rules

**R1: Template-based generation.** External IDs are generated from templates, not random UUIDs. The template includes all identifying information.

**R2: Deterministic inputs only.** Template variables must be stable across re-ingestion:
- ✅ Session ID from harness (stable)
- ✅ Turn index in session (stable)
- ✅ Source adapter name (stable)
- ❌ Random UUID (unstable)
- ❌ Ingest timestamp (unstable)
- ❌ Database auto-increment ID (unstable)

**R3: Same input = same ID forever.** Given the same source artifact bytes, the adapter must generate the same external IDs today, tomorrow, and one year from now.

**R4: Hierarchical uniqueness.** External IDs must be unique within (source, user_id) scope. The database enforces this:
```sql
UNIQUE(source, user_id, external_id) WHERE external_id IS NOT NULL
```

### Template Examples

**Claude Code session start:**
```
{source}:{session_id}:start
# Result: "claude-code:ses_abc123:start"
```

**Claude Code turn:**
```
{source}:{session_id}:turn:{turn_index}
# Result: "claude-code:ses_abc123:turn:5"
```

**Claude Code tool call:**
```
{source}:{session_id}:tool_call:{turn_index}:{tool_index}
# Result: "claude-code:ses_abc123:tool_call:5:0"
```

**Cursor composer (SQLite):**
```
{source}:{bubble_id}:turn:{message_index}
# Result: "cursor:composer-abc123:turn:3"
```

### Stability Violation Example

**BAD — unstable external_id:**
```python
external_id = f"{self.source}:{uuid4()}"  # NEW UUID EVERY TIME
```

**GOOD — stable external_id:**
```python
external_id = f"{self.source}:{session.session_id}:turn:{idx}"
```

**Consequence of violation:** Every daemon sync creates duplicate events. The events table grows without bound. Synthesis produces garbage. The user sees phantom sessions.

---

## §8 The 7 Observe Principles

Every adapter must conform to these principles. See [OBSERVE-PRINCIPLES.md](OBSERVE-PRINCIPLES.md) for the full principles document.

### P1: No Inferred Semantics

**Constraint:** Adapters persist only fields explicitly present in source artifacts. No content-based classifiers. No confidence scores. No model calls.

**Violation example:** Using an LLM to classify turn content as "question" vs "command". This belongs in Map, not Observe.

**Test:** Grep adapter code for any LLM import. Zero matches.

### P2: Nullable Over Guessed

**Constraint:** If a harness doesn't provide `session_id`, `parent_session_id`, `agent_id`, or equivalent, store NULL. Never invent, never infer, never guess.

**Violation example:** Parsing "Project: auth" from message content and setting `project = "auth"`. Only use explicit project metadata fields.

**Test:** Feed fixture missing optional fields. Verify stored values are NULL.

### P3: Lossless Provenance

**Constraint:** Every event carries metadata to locate its origin: harness name, source artifact path, event position, harness-native event type, timestamps.

**Required fields:**
- `source` — harness name
- `source_path` — path to source file
- `source_event_type` — harness-native type string
- `source_line_index` — position in source file
- `timestamp` — event time from harness
- `ingested_at` — ingestion time

**Violation example:** Omitting `source_line_index` and only storing `timestamp`. Makes debugging impossible.

### P4: Raw Preservation or Auditable Redaction

**Constraint:** Retain original payload unless a deterministic redaction rule fires. When redaction happens, mark it in metadata.

**Redaction markers:**
- `content_redacted: true` in extras when content filter modifies text
- `filtered: true` when entire event is blocked

**Violation example:** Silently stripping API keys without marking the event as redacted.

### P5: Append-Only, Ordered Capture

**Constraint:** Never rewrite historical events. Preserve source order. Replaying the same fixture yields the same event sequence.

**Violation example:** Reordering turns by timestamp instead of file line order. If two turns have the same timestamp, order becomes nondeterministic.

**Test:** Ingest fixture twice. Verify event sequence is identical.

### P6: Idempotent and Atomic Ingestion

**Constraint:** Re-ingesting the same artifact produces zero new events. Failed ingests produce zero partial writes.

**Mechanism:** `external_id` uniqueness check before insertion. Database transaction for each session.

**Violation example:** Generating external IDs from random UUIDs. Every re-ingest creates duplicates.

### P7: Failures Are Telemetry

**Constraint:** Parse errors, unknown schemas, adapter mismatches are persisted as anomaly records. Never dropped, never hidden in logs only.

**Anomaly event type:** `ingest.error`

**Required anomaly fields:**
- `source_path` — file that failed
- `error_type` — exception class name
- `session_id` — if known
- Full error message in content

**Violation example:** Logging parse error but not creating an event. The failure becomes invisible to Map layer.

---

## §9 Health Check Protocol

Health checks verify adapter integrity without full ingestion. Run these before and after adapter generation/modification.

### Check 1: Data Paths Exist

**Verify:** All paths referenced in `[discover]` pattern exist and are readable.

**Command:**
```python
paths = adapter.discover()
for path in paths[:10]:  # Sample first 10
    assert path.exists(), f"Path not found: {path}"
    assert os.access(path, os.R_OK), f"Path not readable: {path}"
```

### Check 2: Files Match Discovery Patterns

**Verify:** Discovered files match expected format for the cluster.

**Checks:**
- JSONL: Each non-empty line parses as JSON
- JSON: File parses as valid JSON
- SQLite: File is valid SQLite database (check magic bytes)
- Multi-file: Directory contains expected sub-files
- Markdown: File contains expected delimiters

### Check 3: Most Recent File Parses

**Verify:** The newest file (by mtime) parses without errors.

**Command:**
```python
paths = adapter.discover()
if paths:
    newest = max(paths, key=lambda p: p.stat().st_mtime)
    sessions = list(adapter.iter_sessions())
    assert len(sessions) > 0, "No sessions found in newest file"
    assert all(len(s.turns) > 0 for s in sessions), "Empty sessions found"
```

### Check 4: Format Hasn't Changed

**Verify:** Schema of recent files matches expected descriptor.

**Checks:**
- Required fields present in first few records
- Field types match expectations (timestamp is string/number, content is string/object)
- No unexpected nulls in required fields

### Health Check Failure Example

```
FAIL: Check 3 — Most Recent File Parses
File: ~/.codex/sessions/2025-03-14/session-xyz.jsonl
Error: KeyError: 'payload'

DIAGNOSIS: Codex changed field name from 'payload' to 'response_item'
HEALING: Update descriptor [turn] section, field mappings
```

---

## §10 Generation Decision Tree

When encountering a new harness, decide the adapter implementation strategy:

```
START: New harness discovered
│
├─ Can the descriptor express the format?
│  ├─ Uses JSONL/JSON/SQLite/multifile/markdown pattern? → YES
│  ├─ Fields are statically mappable? → YES
│  ├─ No custom parsing logic needed? → YES
│  └─ WRITE TOML DESCRIPTOR
│
├─ Does the format need custom parsing logic?
│  ├─ Complex nested structures? → YES
│  ├─ Binary formats? → YES
│  ├─ Requires stateful parsing? → YES
│  ├─ Non-standard timestamp formats? → YES
│  └─ GENERATE PYTHON ADAPTER
│
├─ Has the format changed since last generation?
│  ├─ Health check fails? → YES
│  ├─ Fields renamed/moved? → YES
│  ├─ Structure modified? → YES
│  └─ REGENERATE (preserve external_id stability)
│
└─ Is this a new format cluster?
   ├─ Doesn't fit existing 6 clusters? → YES
   └─ RESEARCH → Define new cluster → Document → Implement
```

### Decision Rules

**Write TOML when:**
- Format is one of the 6 known clusters
- Field mappings are static (no conditional logic)
- Timestamps use standard formats
- Content extraction uses registry parsers
- Tool blocks follow Claude-style structure

**Generate Python when:**
- Format requires custom parsing (e.g., ChatGPT's mapping tree)
- Multiple files must be cross-referenced
- Content requires transformation (e.g., Markdown delimiter parsing)
- Timestamps need custom normalization
- Session structure is complex (e.g., parent-child relationships in JSON)

**Regenerate when:**
- Health check detects format drift
- Harness update changed field names
- New fields need to be captured
- BUT: external_id templates must remain stable

---

## §11 Healing Workflow

When an adapter breaks, follow this recovery protocol:

### Step 1: Health Check Detects Failure

Run health checks (§9). Identify which check failed:
- Path not found → harness installation/migration issue
- Parse error → format changed
- Empty sessions → semantic change in data model
- Schema mismatch → field renaming

### Step 2: Read Error Context

Capture:
- Exception type and message
- Source file path
- Line number (if parse error)
- Sample of failing record (first 500 chars)
- Last successful ingestion timestamp

### Step 3: Compare Against Last Working State

Retrieve:
- Previous descriptor/adapter version
- Sample of previously ingested events
- Historical fixtures for this harness

Identify differences:
- Field name changes
- Structure changes
- New required fields
- Removed fields

### Step 4: Regenerate Descriptor or Adapter

**If TOML descriptor:**
- Update field mappings
- Add new optional fields to [metadata]
- Update version number

**If Python adapter:**
- Modify parsing logic
- Add fallback handling for missing fields
- Update docstring with format version

### Step 5: Validate external_id Stability

**Critical:** Ensure regenerated adapter produces identical external_ids for the same input.

**Validation:**
```python
# Re-parse a historical fixture
old_events = old_adapter.parse_fixture(fixture_path)
new_events = new_adapter.parse_fixture(fixture_path)

# External IDs must match exactly
old_ids = {e.external_id for e in old_events}
new_ids = {e.external_id for e in new_events}
assert old_ids == new_ids, "External ID drift detected!"
```

### Step 6: Test Against Fixtures

**Fixture tests:**
- Parse known-good fixtures
- Verify event count matches expected
- Verify key fields populated correctly
- Verify no anomalies created

**Live tests:**
- Run on latest harness data
- Verify no new errors
- Spot-check event content

### Healing Scenario Example

**Initial failure:**
```
ERROR: Codex adapter failed health check
File: ~/.codex/sessions/2025-03/session-abc.jsonl
Error: KeyError: 'payload'
Last success: 2025-03-10 14:00:00 UTC
```

**Investigation:**
- Check previous working file: uses `payload` field
- Check failing file: uses `response_item` field
- Codex version changed between 2025-03-10 and 2025-03-14

**Healing:**
```toml
# OLD (broken)
[turn]
role_field = "payload.role"
content_field = "payload.content"

# NEW (healed)
[turn]
role_field = "response_item.role"
content_field = "response_item.content"

# Add fallback for backward compatibility
[turn.fallback]
role_field = "payload.role"
content_field = "payload.content"
```

**Validation:**
- External ID template unchanged: `{source}:{session_id}:turn:{turn_index}`
- Test fixture from old format: passes
- Test fixture from new format: passes
- Live data ingestion: no errors

---

## §12 Complete Harness Registry

All 20 harnesses with format cluster, data path, status, and adapter type.

| Harness | Format Cluster | Data Path | Status | Adapter Type |
|---------|---------------|-----------|--------|--------------|
| Claude Code | JSONL | `~/.claude/projects/*/sessions/*.jsonl` | ✅ Implemented | Python (reference) |
| Codex | JSONL | `~/.codex/sessions/**/*.jsonl` | 📝 Specified | TOML descriptor |
| OpenClaw | JSONL | `~/.openclaw/agents/*/sessions/*.jsonl` | 📝 Specified | TOML descriptor |
| Pi | JSONL | `~/.pi/sessions/*.jsonl` | 🔍 Identified | TOML descriptor |
| Gemini CLI | JSON | `~/.gemini/tmp/<slug>/chats/session-*.json` | 📝 Specified | TOML descriptor |
| Continue.dev | JSON | `~/.continue/sessions/<id>.json` | 📝 Specified | TOML descriptor |
| ChatGPT | JSON tree | ZIP export, `conversations.json` | 📝 Specified | Python (complex tree) |
| Cursor | SQLite | `~/Library/.../Cursor/User/globalStorage/state.vscdb` | 📝 Specified | TOML descriptor |
| OpenCode | SQLite | `~/.local/share/opencode/opencode.db` | 📝 Specified | TOML descriptor |
| Windsurf | SQLite | Cloud-only with local cache | 🔍 Identified | Research needed |
| Cline | Multi-file | `~/Library/.../saoudrizwan.claude-dev/tasks/<id>/` | 📝 Specified | TOML descriptor |
| Roo Code | Multi-file | `~/Library/.../RooVetGit.roo-code/tasks/<id>/` | 📝 Specified | TOML descriptor |
| Aider | Markdown | `.aider.chat.history.md` in git root | 📝 Specified | Python (delimiter parsing) |
| Amp | Cloud API | GCP, NDJSON via `--stream-json` | 🔍 Identified | Research needed |
| GitHub | Cloud API | `api.github.com` REST API | 📝 Specified | Python (REST adapter) |
| Gmail | Cloud API | `gmail.googleapis.com` | 📝 Specified | Python (OAuth adapter) |
| Hermes | JSONL | `~/.hermes/sessions/*.jsonl` | 🔍 Identified | TOML descriptor |
| Omo | JSONL | `~/.omo/sessions/*.jsonl` | 🔍 Identified | TOML descriptor |
| Zed AI | SQLite | `~/Library/Application Support/Zed/...` | 🔍 Identified | Research needed |
| Warp AI | JSONL | `~/.warp/sessions/*.jsonl` | 🔍 Identified | TOML descriptor |

**Status legend:**
- ✅ Implemented — working adapter exists
- 📝 Specified — format documented, adapter pending
- 🔍 Identified — known to exist, format research needed
- ⛔ Blocked — cloud-only, no local export available

**Adapter type legend:**
- TOML descriptor — format fits descriptor schema
- Python — requires custom parsing logic
- Research needed — insufficient information

---

## Appendix: Quick Reference

### Minimal Descriptor Template

```toml
[adapter]
name = "harness-name"
source = "harness-source"
version = "1.0.0"

[discover]
pattern = "~/.harness/sessions/*"
type = "jsonl"

[session]
id_field = "session_id"

[turn]
role_field = "role"
content_field = "content"
timestamp_field = "timestamp"

[external_id]
template = "{source}:{session_id}:turn:{turn_index}"
```

### Full Claude Code Descriptor

```toml
[adapter]
name = "claude-code"
source = "claude-code"
version = "1.0.0"
description = "Claude Code JSONL session adapter"

[discover]
pattern = "~/.claude/projects/*/sessions/*.jsonl"
type = "jsonl"
recursive = true
follow_symlinks = true

[session]
id_from_path = "session-([a-z0-9-]+)\\.jsonl"
timestamp_from_file = "mtime"
project_from_path = "projects/([^/]+)/sessions"

[turn]
role_field = "type"
role_mapping = { user = "user", assistant = "assistant" }
content_extractor = "extract_text_content"
timestamp_field = "timestamp"
sequence_from = "line_index"

[turn.usage]
input_tokens_field = "message.usage.input_tokens"
output_tokens_field = "message.usage.output_tokens"
cache_read_tokens_field = "message.usage.cache_read_input_tokens"
cache_creation_tokens_field = "message.usage.cache_creation_input_tokens"

[turn.tool_blocks]
extractor = "extract_tool_blocks"
tool_name_field = "name"
tool_id_field = "id"

[metadata]
model_field = "message.metadata.model"
stop_reason_field = "message.stop_reason"
git_branch_field = "message.metadata.git_branch"

[external_id]
session_template = "{source}:{session_id}:start"
turn_template = "{source}:{session_id}:turn:{turn_index}"
tool_call_template = "{source}:{session_id}:tool_call:{turn_index}:{tool_index}"
tool_result_template = "{source}:{session_id}:tool_result:{turn_index}:{tool_index}"
```

### Validation Checklist

Before marking an adapter complete:

- [ ] `discover()` returns paths for existing harness data
- [ ] `iter_sessions()` yields sessions with valid `ObservedSession` objects
- [ ] All sessions have non-null `session_id`, `source_path`, `start_time`
- [ ] All turns have non-null `role`, `content`, `timestamp`
- [ ] `external_id` is deterministic (same input = same ID)
- [ ] Re-ingestion produces zero new events (idempotency)
- [ ] Parse errors create `ingest.error` events (Principle 7)
- [ ] No LLM calls, no heuristics, no inferred semantics (Principle 1)
- [ ] Optional fields are NULL when not provided (Principle 2)
- [ ] Provenance fields populated (source_path, source_event_type, etc.)
- [ ] Health check passes on recent harness data
- [ ] Test fixtures pass with expected event counts

---

*Document version: adapter-protocol-1.0*  
*References: [OBSERVE-PRINCIPLES.md](OBSERVE-PRINCIPLES.md), [OBSERVE-SCHEMA.md](OBSERVE-SCHEMA.md), [FEDERATION-ARCHITECTURE.md](FEDERATION-ARCHITECTURE.md)*  
*Source files: `syke/ingestion/observe.py`, `syke/ingestion/parsers.py`*
