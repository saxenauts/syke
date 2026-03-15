# Federation Architecture — Observe Layer

> How Syke captures data from multiple harnesses through a single canonical schema.

---

## What's Built

### ObserveAdapter ABC Contract

The federation boundary is a simple abstract base class:

```python
class ObserveAdapter(ABC):
    @abstractmethod
    def discover(self) -> list[Path]:
        """Find harness data files/directories on disk."""
        pass
    
    @abstractmethod
    def iter_sessions(self, since: datetime | None = None) -> Iterable[ObservedSession]:
        """Yield sessions with their events."""
        pass
```

That's the entire contract. Every harness adapter implements these two methods. The rest is implementation detail.

### ClaudeCodeAdapter: The Reference Implementation

ClaudeCodeAdapter is the first and only Observe adapter currently implemented. It reads Claude Code JSONL session files:

```
~/.claude/sessions/
  └── sessions/
        └── 2025-03-14/
              └── session-abc123.jsonl
```

**What it captures:**
- Per-turn events (user, assistant messages)
- Token metrics (input, output, cache_read)
- Model names, stop reasons, roles
- Session metadata (title, project, duration)
- Full tool I/O embedded as content markers

**Universal parsers used:**
- `read_jsonl()` — line-delimited JSON parsing
- `parse_timestamp()` — ISO8601 timestamp normalization
- `extract_text_content()` — message content extraction
- `measure_content()` — content size metrics

### Canonical Schema

All adapters compile into the same typed schema:

```sql
events (
  id, timestamp, source, event_type, content, user_id,
  external_id, ingested_at, title,
  session_id, parent_session_id, sequence_index,
  role, model, stop_reason,
  input_tokens, output_tokens, cache_read_tokens, is_error,
  source_event_type, source_path, source_line_index,
  extras
)
```

See [OBSERVE-SCHEMA.md](OBSERVE-SCHEMA.md) for full schema documentation.

### Format Clusters Identified

Analysis of 10,000+ potential harnesses identified 6 format clusters:

| Cluster | Format | Examples | Status |
|---|---|---|---|
| JSONL | Line-delimited JSON | Claude Code, Codex, Pi | **Implemented** (Claude Code) |
| JSON | Single file per session | Gemini CLI, Continue.dev | Identified, no adapter |
| SQLite | Database tables | OpenCode, Cursor, Windsurf | Identified, no adapter |
| Multi-file | Directory per task | Cline, Roo Code | Identified, no adapter |
| Markdown | Freeform text | Aider | Identified, no adapter |
| Cloud API | HTTP/REST | Amp, remote agents | Identified, no adapter |

Only JSONL (Claude Code) has an implemented adapter. The other clusters are documented for future work.

### Deployment Types Identified

Three deployment patterns cover all harnesses:

| Type | How Syke Would Capture | Examples | Status |
|---|---|---|---|
| Local CLI | File-based ingestion | Claude Code, Codex, Aider | **Working** (Claude Code via discover/iter_sessions) |
| IDE Extension | Extension storage path | Cursor, Cline, Roo, Continue.dev, Windsurf | Identified, no implementation |
| Cloud API | API polling or webhook | Amp, remote agents, CI/CD pipelines | Identified, no implementation |

---

## Anti-Pattern: Memorix's Shared Directory Federation

Memorix (AVIDS2/memorix) uses a shared-directory model: all agents write to `~/.memorix/data/<projectId/>`. This is NOT federation — it's centralized storage requiring agent cooperation. If an agent doesn't write to the right directory, its data is invisible.

**Why this fails at scale:**
- Requires every harness to cooperate with a specific storage convention
- No provenance — can't tell which agent wrote what
- No conflict resolution — two agents writing the same key overwrites silently
- Doesn't work for cloud-based harnesses (Amp, remote Cursor)

Syke's adapter model avoids this by reading harness-native formats directly.

---

## Federation Invariants

1. **One schema, many adapters.** The events table is the IR. Adapters compile into it.
2. **Provenance on every event.** Source, source_path, source_event_type, adapter version.
3. **Time is the only correlation constant.** Cross-harness linking uses timestamps, not shared IDs.
4. **Observe doesn't link. Map links.** Session grouping across harnesses is a Map concern.
5. **Conflicts are data.** Store both sides. Never resolve at capture time.
6. **Adapters are code. Schema is stable.** New harness = new adapter. Schema changes only for new cross-harness primitives.

---

## Research Directions

The following are NOT implemented. They are documented as research directions for future work.

### Hook Listener for Real-Time Capture

A local HTTP endpoint that Claude Code (and potentially other hook-enabled harnesses) could POST to for sub-100ms capture latency.

**Not implemented.** The current implementation polls files via `discover()` + `iter_sessions()`.

### File Watcher for Non-Hook Harnesses

Using watchdog/fsevents to monitor harness directories for changes, enabling near-real-time ingestion without hooks.

**Not implemented.** Current ingestion is batch-oriented via daemon sync.

### Agent-Generated Adapters

For 10,000+ harnesses, manually writing every adapter doesn't scale. Research direction: agents that read harness documentation and generate adapter code.

**Input**: Harness documentation (API docs, file format spec, example sessions)
**Output**: Adapter implementing `discover()` + `iter_sessions()`

**Not implemented.** Only ClaudeCodeAdapter exists, written by hand.

### Cross-Harness Correlation

**Problem**: User works in Claude Code for 2 hours, switches to Cursor for 30 minutes, then back to Claude Code. Three different harnesses, one continuous work session.

**Observe's role**: Capture events from all three with timestamps and source. Do NOT try to link them at Observe time.

**Map's role**: Discover that events from claude-code (session A), cursor (session B), and claude-code (session C) all touch the same git repository, same branch, overlapping time window → group as one "work session" retrospectively.

**What Observe provides for this:**
- `timestamp` — temporal alignment
- `source` — which harness
- `extras.git_branch`, `extras.cwd` — shared project context
- `session_id` — per-harness session grouping

Map uses these signals to build cross-harness session graphs. Observe just captures them.

**Not implemented.** The signals are captured. No correlation code exists.

### External MCP Server

Syke as an MCP server providing `ask`, `context`, `record` tools that any harness could call during sessions.

**Not implemented.** MCP server code was started but is explicitly commented out.

### Epistemic Conflict Detection

**Problem**: Claude Code says the auth module uses JWT. Codex says it uses OAuth2. Both captured by Observe.

**Observe's role**: Store both claims with origin, time, and context. Never choose a winner.

**Map's role**: Detect the conflict. Surface both to the user. Let the user or a future resolution agent decide.

**This is a feature, not a bug.** Cross-harness disagreements are data about how different agents interpret the same codebase.

**Not implemented.** The data structure supports it. No detection code exists.

### Tiered Real-Time Architecture

A vision for capture latency tiers:

- **Tier 1**: Hook-based (<5ms) — HTTP POST from hook-enabled harnesses
- **Tier 2**: File watch (10-50ms) — watchdog monitoring harness directories
- **Tier 3**: MCP retrieval (<150ms) — Syke as MCP server for in-session queries
- **Tier 4**: Daemon batch (15 min) — safety net + synthesis trigger

**Not implemented.** Zero tiers are operational. Current implementation is Tier 4 only (15-minute daemon sync).

---

## What's Built vs What's Research

**Built (observe-phase2):**
- ObserveAdapter ABC with `discover()` + `iter_sessions()` contract
- ClaudeCodeAdapter as reference implementation
- Canonical schema with typed columns
- Universal parsers for JSONL format
- 6 format clusters identified (only 1 has adapter)
- 3 deployment types identified (only 1 has implementation)
- 7 Observe Principles enforced and tested

**Research directions (not built):**
- Hook listener for real-time capture
- File watcher for non-hook harnesses
- Agent-generated adapters
- Cross-harness correlation in Map layer
- Epistemic conflict detection
- External MCP server
- Tiered real-time architecture

---

*Document version: observe-phase2*  
*Tests passing: 363*
