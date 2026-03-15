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

The flexibility for different adapters comes from CODE (agents generate adapter code), not from the storage schema. There are ~10,000 harnesses but only ~6 format clusters and ~3 deployment types. The adapter handles the variability. The schema stays clean and typed.

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

  -- CAUSALITY
  parent_event_id TEXT,

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

  -- PROVENANCE
  source_event_type TEXT,
  source_path TEXT,
  source_line_index INTEGER,

  -- NARROW ESCAPE HATCH
  extras TEXT DEFAULT '{}'
)
```

### Column Justification

**Universal fields** — present on every event from every harness.

**Grouping hints** — `session_id` and `parent_session_id` are present when the harness provides session concepts. NULL for event-driven agents, continuous streams, or harnesses without sessions. Map uses these to discover session structure, but doesn't depend on them.

**sequence_index** — total order within a session. Replaces `turn_index` because it works for turns AND tool events. When tool_call and tool_result become separate events, turn_index breaks (it only counts user/assistant messages). sequence_index counts all events in session order.

**parent_event_id** — explicit causality. "This tool_result was caused by this tool_call" is different from "these happened in the same session." Enables:
- RLM trajectory replay (exact causal chain)
- GEPA execution traces (action → outcome links)
- SHARP failure attribution (which call failed and what it caused)

**source_event_type** — the harness-native type string ('assistant', 'progress', 'queue-operation'). Preserved alongside our canonical `event_type` for provenance. When harness format changes, this field shows what the source actually said.

**duration_ms** — how long the event took. Cheap to store. Critical for GEPA performance metrics, Reflexion self-evaluation, latency analysis.

**cache_creation_tokens + cache_read_tokens** — paired token metrics for Context Engineering research (cache hit rate analysis, 93.5% in Anthropic's production data).

**extras** — narrow escape hatch for genuinely variable harness-specific fields. Policy: a field gets a typed column only if stable across 2+ harnesses AND likely in WHERE/GROUP BY. Everything else → extras.

### Event Type Taxonomy

| event_type | What | Content |
|---|---|---|
| `session.start` | Session envelope | Metadata summary (project, duration, turn counts) |
| `turn` | User or assistant message | The actual message text |
| `tool_call` | Agent invokes a tool | Full input parameters as JSON |
| `tool_result` | Tool returns output | Full output content |
| `ingest.error` | Parse/filter failure | Error description with provenance |
| `session` | Legacy (pre-Observe) | Old session blob (backward compat) |

### Dedup Strategy

Partial unique index: `UNIQUE(source, user_id, external_id) WHERE external_id IS NOT NULL`

Old UNIQUE(source, user_id, timestamp, title) constraint kept for legacy events only.

### Index Strategy

```sql
CREATE INDEX idx_events_session ON events(session_id) WHERE session_id IS NOT NULL;
CREATE INDEX idx_events_parent_session ON events(parent_session_id) WHERE parent_session_id IS NOT NULL;
CREATE INDEX idx_events_parent_event ON events(parent_event_id) WHERE parent_event_id IS NOT NULL;
CREATE INDEX idx_events_tool_name ON events(tool_name) WHERE tool_name IS NOT NULL;
CREATE INDEX idx_events_model ON events(model) WHERE model IS NOT NULL;
CREATE INDEX idx_events_type_time ON events(event_type, timestamp);
```

### Migration

One-time script: parse existing `metadata` JSON → populate typed columns → move leftovers to `extras` → deprecate `metadata`.

---

## Research Frameworks Served

| Framework | What It Queries | Schema Support |
|---|---|---|
| RLM (arXiv:2512.24601) | Turn chains, output→input loops | parent_event_id, sequence_index |
| GEPA (arXiv:2507.19457) | Execution traces, performance metrics | duration_ms, tool events, causality |
| Context Engineering (arXiv:2603.09023) | Token budgets, cache efficiency | input/output/cache_read/creation_tokens |
| SHARP (arXiv:2602.08335) | Failure traces, recovery patterns | is_error, parent_event_id, tool_name |
| ACE (arXiv:2310.06775) | Self-evolution operational data | Complete event stream per agent |
| Reflexion (arXiv:2303.11366) | Self-evaluation traces | duration_ms, stop_reason, model |
| Characterization Tests | Session replay | source_event_type, sequence_index, provenance |

---

## Design Principles Applied

**P11 "Let Structure Emerge"** — applied to Map layer, NOT Observe. Observe has known structure; Map has emergent structure.

**"Adapters are compilers"** — flexibility lives in generated adapter code. Schema is the stable compilation target. New harness = new adapter code, same schema.

**"Code is generation now"** — agents generate adapter code from harness documentation. The adapters compile into the canonical schema. The schema doesn't change per harness.

**"Neutral observation with time as the only constant"** — events are the universal primitive. Time and source are always present. Everything else is nullable. No assumptions about session structure, turn patterns, or tool usage.

**"The event is the atom"** — not the run, not the turn, not the tool call. Events. In time. From sources. With typed fields when the source provides them.

---

## Key Learnings

1. **"Let structure emerge" has a scope.** It's correct for adaptive memory (Map). It's incorrect for deterministic capture (Observe). Know which layer needs emergence vs determinism.

2. **Freeform JSON hides known structure.** When you put integers into JSON into a TEXT column, you're not being flexible — you're being evasive. If you know the type, declare the type.

3. **The adapter is the flexible layer, not the schema.** 10,000 harnesses, 6 format clusters, 3 deployment types. The adapters absorb the variety. The schema stays stable.

4. **Normalized tables assume today's patterns are universal.** runs/turns/tool_calls works for human-agent sessions. It breaks for swarms, continuous streams, self-training loops. Flat events with typed columns are more future-proof.

5. **Causality needs explicit links.** session_id gives grouping. parent_event_id gives causality. These are different.
