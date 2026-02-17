# Claude Code Session Extraction Strategy

## Source
Local JSONL files from `~/.claude/`

## Authentication
None required — local filesystem access.

## Data Stores (Dual-Store Architecture)

### 1. Project Store (preferred)
- **Path**: `~/.claude/projects/{encoded-path}/*.jsonl`
- **Format**: Rich session files with full message content, tool usage, git context
- **Directory naming**: Path segments joined by `-` (e.g., `-Users-jane-Documents-myproject`)
- **Includes**: User messages, assistant responses, progress events, summaries
- **Metadata**: session ID, git branch, cwd, project path, tool usage counts

### 2. Transcript Store (fallback)
- **Path**: `~/.claude/transcripts/ses_*.jsonl`
- **Format**: Lightweight session files with user messages and tool calls
- **Used for**: Sessions not found in the project store
- **Metadata**: session ID, tool calls, duration

## Extraction Approach
1. **Pass 1**: Parse all project-store sessions (richer data)
2. **Pass 2**: Parse transcript-store sessions, skip any already seen in Pass 1
3. **Dedup**: By session filename stem across both stores
4. **mtime optimization**: Skip files with mtime before last sync timestamp

## Event Type
- `session` — one event per Claude Code session (not per message)

## Content Processing
- User messages extracted as the primary semantic content
- Assistant messages included (truncated to 2K chars each) for context on what was built
- `<system-reminder>` tags stripped — they're scaffolding, not user signal
- Agent scaffolding sections (certainty protocols, decision frameworks) stripped
- Content capped at 50K chars per session
- Sessions < 50 chars skipped as noise

## Key Fields
- **timestamp**: First message's ISO timestamp or epoch millis
- **title**: First line of first user message (truncated to 120 chars)
- **content**: All user messages joined by `---` separators, plus assistant responses
- **metadata**: session_id, store (project/transcript), project path, git_branch, cwd, user_messages count, assistant_messages count, tools_used, duration_minutes, summary (if available)

## Path Decoding
The project directory name encodes the filesystem path with `-` replacing `/`. Since directory names themselves can contain hyphens (e.g., `claude-hack`), we use DFS backtracking against the actual filesystem to resolve ambiguity.

Fallback: naive replacement of `-` with `/` when the path no longer exists on disk.

## Privacy
- **Classification**: Private source — requires explicit consent (`--yes` flag or interactive prompt)
- **Content filter**: Runs ContentFilter to skip private messaging sessions and redact credentials

## Known Issues
- Very long sessions (>50K chars) are truncated — some context may be lost
- Transcript-store sessions lack assistant responses and git context
- Project directory decoding can fail for deleted/moved projects (falls back to naive path)
