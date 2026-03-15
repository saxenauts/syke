# Observe Canonical Schema — Design Rationale

> Internal research document. Records the reasoning chain behind the Observe storage model.

---

## The Core Claim

**The event is the universal primitive. Not the run. Not the turn.**

Time is the only constant. Source is always known. Everything else is a hint, not a structural requirement. The adapter is the compiler. The events table is the IR. Typed columns are the known fields of the IR. NULLs mean "this field doesn't apply to this source."

---

## Why Not Normalized Tables (runs, turns, tool_calls)

We considered normalized tables where each execution primitive gets its own table. Oracle validated this as architecturally clean. We rejected it because:

**The future is unpredictable.** Today's harnesses have sessions with turns. Tomorrow's won't:

| Harness Pattern | "Run"? | "Turn"? | "Tool Call"? |
|---|---|---|---|
| Claude Code (today) | Yes — 1 JSONL file | Yes — user→agent cycle | Yes — explicit |
| 50-agent swarm (tomorrow) | No clear boundary | No — agents talk to each other | Yes but interleaved |
| CI/CD agent pipeline | Continuous — no session | No — event-driven | Yes but automated |
| RLM self-training loop | Recursive — sessions within sessions | Unclear — agent prompts itself | Yes |
| 3 agents on same codebase | Overlapping — retrospective construct | Per-agent maybe | Per-agent |

Normalized tables bake today's patterns (sessions, turns, tool calls) into the schema. Flat events with typed columns let Map discover patterns that don't exist yet.

---

## Why Not JSON Metadata Blob

**"Let structure emerge" was applied incorrectly.** That principle belongs in the Map layer (adaptive memory), not Observe (deterministic capture).

Observe captures KNOWN structure. Harness formats are documented. Token metrics are always integers. Model names are always strings. Session IDs are always UUIDs. Putting these in a freeform JSON blob doesn't let structure emerge — it HIDES structure that already exists.

The flexibility for different adapters comes from adapter implementations, not from the storage schema. There are ~10,000 harnesses but only ~6 format clusters and ~3 deployment types. The adapter handles the variability. The schema stays clean and typed.

**Practical consequences of JSON blob:**
- `json_extract(metadata, '$.usage.input_tokens') > 8000` — no index, full scan
- Agent needs to know exact JSON path — not discoverable
- One typo in path = silent zero results
- Can't GROUP BY, can't aggregate without parsing

---

## The Canonical Schema

```sql
events (
  -- UNIVERSAL (always present)
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  source TEXT NOT NULL,
  event_type TEXT NOT NULL,
  content TEXT NOT NULL,
  user_id TEXT NOT NULL,
  external_id TEXT,
  ingested_at TEXT,
  title TEXT,

  -- GROUPING (nullable hints for Map)
  session_id TEXT,
  parent_session_id TEXT,

  -- ORDERING
  sequence_index INTEGER,

  -- TYPED KNOWN FIELDS (nullable, from harness)
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

  -- CAUSALITY
  parent_event_id TEXT,

  -- PROVENANCE
  source_event_type TEXT,
  source_path TEXT,
  source_line_index INTEGER,

  -- NARROW ESCAPE HATCH
  extras TEXT DEFAULT '{}',

  -- BACKWARD COMPAT (legacy events only)
  metadata TEXT
)
```

### Column Justification

**Universal fields** — present on every event from every harness.

**Grouping hints** — `session_id` and `parent_session_id` are present when the harness provides session concepts. NULL for event-driven agents, continuous streams, or harnesses without sessions. Map uses these to discover session structure, but doesn't depend on them.

**sequence_index** — total order within a session. Replaces `turn_index` because it works for turns and other event types. Captures the original line order from JSONL files.

**Typed token columns** — `input_tokens`, `output_tokens`, `cache_read_tokens` are captured directly from Claude Code JSONL usage data. Used for cost analysis and context engineering research.

**role** — 'user', 'assistant', or harness-native role. Synthesis reads this directly without json_extract.

**model** — the actual model name used (e.g., 'claude-sonnet-4-20250514'). Stored as typed column, not buried in extras.

**stop_reason** — why the turn ended ('end_turn', 'tool_use', 'max_tokens', etc.).

**is_error** — boolean flag for turns where stop_reason indicates an error condition.

**source_event_type** — the harness-native type string ('assistant', 'progress', 'queue-operation'). Preserved alongside our canonical `event_type` for provenance. When harness format changes, this field shows what the source actually said.

**extras** — narrow escape hatch for genuinely variable harness-specific fields. Policy: a field gets a typed column only if stable across 2+ harnesses AND likely in WHERE/GROUP BY. Everything else → extras.

**metadata** — kept for backward compatibility with events ingested before the Observe schema. New events populate typed columns directly. Synthesis queries handle both old (metadata JSON) and new (typed columns) transparently.

### Event Type Taxonomy

| event_type | What | Content |
|---|---|---|
| `session.start` | Session envelope | Metadata summary (project, duration, turn counts) |
| `turn` | User or assistant message | The actual message text (includes tool_use/tool_result markers) |
| `ingest.error` | Parse/filter failure | Error description with provenance |
| `session` | Legacy (pre-Observe) | Old session blob (backward compat) |

**Tool calls are separate events.** Each tool_use block becomes a `tool_call` event with `tool_name`, `tool_correlation_id`, and full input JSON as content. Each tool_result block becomes a `tool_result` event with full output as content and `is_error` flag. Events are linked via `parent_event_id`: tool_result → tool_call → assistant turn. Turn content contains only text and thinking blocks.

### Dedup Strategy

Partial unique index: `UNIQUE(source, user_id, external_id) WHERE external_id IS NOT NULL`

This enables idempotent re-ingestion of the same artifact without creating duplicate events. The legacy UNIQUE(source, user_id, timestamp, title) constraint still exists for backward compatibility with older events.

### Index Strategy

```sql
CREATE INDEX idx_events_session ON events(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX idx_events_parent_session ON events(parent_session_id) WHERE parent_session_id IS NOT NULL;
CREATE INDEX idx_events_model ON events(model) WHERE model IS NOT NULL;
CREATE INDEX idx_events_type_time ON events(event_type, timestamp);
```

---

## What's Actually Populated

**Always populated (every event):**
- id, timestamp, source, event_type, content, user_id

**Populated from harness data:**
- external_id (Claude Code's message UUID)
- ingested_at (ingestion timestamp)
- title (session title when available)
- session_id, parent_session_id (when harness provides them)
- sequence_index (line order in source file)
- role, model, stop_reason (per-turn metadata)
- input_tokens, output_tokens, cache_read_tokens (usage data)
- is_error (derived from stop_reason)
- source_event_type, source_path, source_line_index (provenance)
- extras (harness-specific extensions)

**NOT implemented (do not use):**
- parent_event_id — removed, never populated
- tool_name — removed, never populated
- tool_correlation_id — removed, never populated
- duration_ms — removed, never populated
- cache_creation_tokens — removed (use extras if available from harness)

**Backward compat only:**
- metadata — JSON blob from pre-Observe ingestion. New events use typed columns + extras.

---

## Key Learnings

1. **"Let structure emerge" has a scope.** It's correct for adaptive memory (Map). It's incorrect for deterministic capture (Observe). Know which layer needs emergence vs determinism.

2. **Freeform JSON hides known structure.** When you put integers into JSON into a TEXT column, you're not being flexible — you're being evasive. If you know the type, declare the type.

3. **The adapter is the flexible layer, not the schema.** The adapters absorb the variety. The schema stays stable.

4. **Normalized tables assume today's patterns are universal.** runs/turns/tool_calls works for human-agent sessions. It breaks for swarms, continuous streams, self-training loops. Flat events with typed columns are more future-proof.

5. **Typed columns enable direct queries.** Synthesis reads `role`, `model`, `input_tokens` directly. No json_extract. No path guessing. No silent failures from typos.

---

## Research Directions

The schema design supports these potential research areas:

| Framework | What It Would Query | Schema Support |
|---|---|---|
| RLM (arXiv:2512.24601) | Turn chains, output→input loops | sequence_index for ordering |
| GEPA (arXiv:2507.19457) | Execution traces, performance metrics | token counts, timestamps |
| Context Engineering (arXiv:2603.09023) | Token budgets, cache efficiency | input/output/cache_read_tokens |
| SHARP (arXiv:2602.08335) | Failure traces, recovery patterns | is_error, stop_reason |
| ACE (arXiv:2310.06775) | Self-evolution operational data | Complete event stream per agent |
| Reflexion (arXiv:2303.11366) | Self-evaluation traces | stop_reason, model |

These frameworks are research directions. The schema provides the substrate. The actual correlation and analysis logic belongs in Map.

---

*Document version: observe-phase2*  
*Schema validated against: syke/memory/schema.py*
