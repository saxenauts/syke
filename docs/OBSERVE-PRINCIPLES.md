# Observe Layer Principles

> Syke Observe runtime is a pure-capture telemetry layer. No LLM in the ingest boundary. No meaning-making.
> This document defines the testable capture boundary, not every helper module that happens to live under `syke/observe/`.

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

**Test**: The ingest runtime must not require an LLM call to decide what events to persist.

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

## Transport Modes

Real-time and batch are both transport modes into the same deterministic boundary. The 7 principles apply equally regardless of whether an event arrives via HTTP hook (<100ms), file watcher (<100ms), native stream (streaming), or periodic poll (15 min). Transport is a concern of speed and reliability. Compilation is a concern of correctness and determinism. They are separate.

MCP is participation, not observation. An MCP server registered with a harness activates only when the agent calls its tools. There is no "subscribe to session events" API in MCP. Use hooks and file watchers for observation. Use MCP and SKILL.md for capability injection and memory distribution.

---

## Federation Thesis

Syke wins as unified memory because it observes all harnesses neutrally. Each harness has its own compaction strategy, context management, and epistemic assumptions. Syke doesn't take sides — it captures raw data from all of them.

**Neutral observation enables federation only when paired with strong provenance.**

Different session models (Claude Code: JSONL files, Cursor: SQLite, Aider: Markdown) are not contradictions — they're different capture units grouped under the same adapter contract: `discover() + iter_sessions()`.

Different compaction strategies are telemetry, not problems. If Gemini emits explicit summary records and Claude compacts internally, that asymmetry is data for Map to study.

Epistemic conflicts between harnesses are features, not bugs. Store both claims with origin, time, and context. Never choose a winner at Observe time.

---

## Package Note

The `syke/observe/` package also contains factory/control-plane code for generating and healing adapters. That does not weaken the rule above. The boundary is:

- Observe runtime: deterministic capture into the immutable timeline
- Observe factory/control plane: optional agent help for producing adapters that feed that runtime

## What Observe Does NOT Do

- No agent type classification (Map)
- No parent confidence scoring (Map)
- No Five Signals extraction (Map)
- No adaptive content previewing (Synthesis/Ask)
- No LLM calls inside the ingest boundary (Map/Ask and factory/control plane are outside that boundary)
- No summary generation (Map)
- No compaction handling (harness's responsibility)
- No semantic deduplication (Map)
- No cross-platform correlation (Map)

---

## Research Directions

The Observe layer could serve as raw data substrate for future research:
- Recursive Language Model (RLM) research — self-referential learning from past sessions
- GEPA cycles (Goal, Execution, Performance, Adaptation) — operational history for self-improvement
- ACE (Autonomous Cognitive Entity) — self-evolving agent architecture
- Cross-harness epistemic analysis — how different agents handle the same problem

Pure capture is the correct substrate because these research directions need raw operational history, not pre-digested interpretations that bake in today's assumptions.

---

*Document version: observe-phase2*  
*Tests passing: 363*
