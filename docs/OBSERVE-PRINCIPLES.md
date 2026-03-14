# Observe Layer Principles

> Syke Observe is a pure-capture telemetry layer. No LLM. No heuristics. No meaning-making.
> All intelligence belongs in Map. This document defines the testable boundary.

---

## The Boundary Test

> "If two competent implementers could disagree about the output for the same input artifact, the logic is too smart for Observe."

Observe does mechanical parsing. Map does reasoning.

```
OBSERVE (deterministic):                MAP (requires judgment):
"This JSONL line has type=user"         "This session is about auth refactoring"
"parentSessionId field = ses_abc"       "This agent is a librarian"
"File has 47 lines"                     "These two sessions are related"
"Credential pattern matched, redacted"  "The user changed their mind mid-session"
"Timestamp is 2024-01-23T10:00:00Z"     "This compaction lost important context"
```

---

## Seven Principles

### P1: No Inferred Semantics

Observe persists only fields explicitly present in source artifacts or mechanically derived from transport/provenance. No content-based classifiers, confidence scores, summaries, or model calls exist in Observe code.

**Test**: Grep Observe code for any LLM import, any classifier, any `if "pattern" in content` heuristic. Zero matches.

### P2: Nullable Over Guessed

If a harness doesn't explicitly provide `session_id`, `parent_session_id`, `agent_id`, or equivalent, Observe stores NULL. Never invent, never infer, never guess.

**Test**: Feed fixture inputs missing those fields. Verify stored values are NULL.

### P3: Lossless Provenance

Every persisted event carries enough metadata to locate and explain its origin: harness name, source artifact path, event position within artifact, harness-native event type, event timestamp, ingest timestamp.

**Test**: Every Observe-ingested event has non-null source, external_id, and timestamp. Events trace back to exact source artifacts.

### P4: Raw Preservation or Auditable Redaction

Retain original payload unless a deterministic redaction rule fires. When redaction happens, the record shows it happened — a marker, not silent mutation.

**Test**: Feed fixture with embedded credential pattern. Verify content is redacted AND metadata contains redaction indicator.

### P5: Append-Only, Ordered Capture

Never rewrite historical events. Preserve source order within each capture unit. Replaying the same fixture yields the same event sequence.

**Test**: Ingest fixture twice. Verify event sequence is identical. No existing events modified.

### P6: Idempotent and Atomic Ingestion

Re-ingesting the same artifact produces zero new events. A failed ingest produces zero partial writes — full rollback via transaction.

**Test**: Run ingest, count events. Run again, count unchanged. Inject crash mid-session, verify zero partial records.

### P7: Failures Are Telemetry

Unknown schemas, parse errors, partial reads, adapter mismatches are persisted as anomaly records with full provenance. Never dropped, never hidden in logs only.

**Test**: Feed malformed JSONL fixture. Verify an anomaly event exists with `event_type="ingest.error"`, source info, and error detail.

---

## Federation Thesis

Syke wins as unified memory because it observes all harnesses neutrally. Each harness has its own compaction strategy, context management, and epistemic assumptions. Syke doesn't take sides — it captures raw data from all of them.

**Neutral observation enables federation only when paired with strong provenance.**

Different session models (Claude Code: JSONL files, Cursor: SQLite, Aider: Markdown) are not contradictions — they're different capture units grouped under the same adapter contract: `discover() + iter_sessions()`.

Different compaction strategies are telemetry, not problems. If Gemini emits explicit summary records and Claude compacts internally, that asymmetry is data for Map to study.

Epistemic conflicts between harnesses are features, not bugs. Store both claims with origin, time, and context. Never choose a winner at Observe time.

---

## Telemetry-as-Spec (from "Production Telemetry Is the Spec That Survived")

Observe is the telemetry collector. It captures the accumulated record of what agents were asked to do and how they responded — the spec that survived when everything else (docs, tests, specs) rotted.

```
Observe = "what happened" layer (raw telemetry)
Map     = "what contract does that imply" layer (behavioral extraction)
Ask     = "queryable spec an agent can consult" layer (memex)
```

The honest sequence: Observe production → extract behavioral contracts → encode as spec → bring the agent in.

---

## What Observe Does NOT Do

- No agent type classification (Map)
- No parent confidence scoring (Map)
- No Five Signals extraction (Map)
- No adaptive content previewing (Synthesis/Ask)
- No LLM calls of any kind (Map/Ask)
- No summary generation (Map)
- No compaction handling (harness's responsibility)
- No semantic deduplication (Map)
- No cross-platform correlation (Map)

---

## Research Foundation

The Observe layer serves as the raw data substrate for:
- Recursive Language Model (RLM) research — self-referential learning from past sessions
- GEPA cycles (Goal, Execution, Performance, Adaptation) — operational history for self-improvement
- ACE (Autonomous Cognitive Entity) — self-evolving agent architecture
- Cross-harness epistemic analysis — how different agents handle the same problem

Pure capture is the correct substrate because these research directions need raw operational history, not pre-digested interpretations that bake in today's assumptions.
