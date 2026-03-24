# Observe Architecture

Observe is Syke's deterministic capture layer. It ingests activity from AI harnesses into a canonical event ledger. No LLM in the capture path. Same input -> same events. Time axis, append-only.

Observe has two concerns: **transport** (getting data to Syke in real time) and **compilation** (parsing harness-native format into canonical events). Transport is best-effort and freshness-oriented. Compilation is deterministic. Both feed the same immutable ledger.

---

## Architecture Diagram

```
HARNESS PLANE
  Each harness has TWO installed SKILL.md packages:
  ├── syke-context/          (outbound: memory → harness)
  └── syke-observe-<harness>/ (inbound: harness → Syke, with install/health/heal)
  
  Data surfaces: hooks │ native streams │ session files │ SQLite DBs

OBSERVE RUNTIME (two processes)
  observe-rt    → event-driven capture (hooks, file watch, native streams)
  observe-sweep → periodic reconcile + backfill (current daemon, safety net)

COMPILE PATH (shared by both runtimes)
  capture notification → targeted adapter ingest → ContentFilter
  → canonical events ledger (SQLite, append-only, external_id dedupe)
  → anomalies as telemetry (ingest.error events)

INTELLIGENCE BRIDGE
  new ledger rows → dirty marker → debounced synthesis/distribution
  → refreshed context skills pushed back into harnesses
```

---

## Vocabulary

Three names, used consistently everywhere:

**Observe Adapter**: The deterministic compiler inside Syke. Python class extending ObserveAdapter. Implements `discover()` + `iter_sessions()`. Produces ObservedSession objects.

**Connector Skill**: The installed `syke-observe-<harness>/SKILL.md` package in the harness. Contains: install instructions, health check recipe, heal/repair steps, transport configuration (which hooks to enable, which files to watch). This is a formal SKILL.md per the Agentic AI Foundation standard (agentskills.io).

**Context Skill**: The installed `syke-context/SKILL.md` package in the harness. Provides memory context to the agent. The outbound half of the bidirectional loop.

The adapter factory is the current mechanism for generating, testing, and healing adapters when native support is missing or drifting.

---

## Transport Tiers

Real-time is fundamental, not optional. Four transport modes, highest fidelity first:

| Tier | Mode | Latency | Mechanism | When to Use |
|---|---|---|---|---|
| 1 | `hook` | <100ms | HTTP POST from harness lifecycle events | Harness supports hooks (Claude Code: 14 events) |
| 2 | `watch` | <100ms | FSEvents/inotify on session files/DBs | Universal fallback for file-backed harnesses |
| 3 | `native` | streaming | SSE, stdout pipe, WebSocket | Platform-specific optimization (OpenCode SSE, Codex --json) |
| 4 | `poll` | 15 min | Periodic `discover()` + `iter_sessions()` | Safety net and reconciliation only |

MCP is NOT an observation transport. MCP is participation — the agent calls tools. You cannot subscribe to session events via MCP. Use hooks for observation, MCP for capability injection.

Current transport mapping:

| Harness | Tier 1 (hook) | Tier 2 (watch) | Tier 3 (native) | Tier 4 (poll) |
|---|---|---|---|---|
| Claude Code | HTTP hooks (14 events) | JSONL tail | — | ✅ |
| Codex | — | JSONL tail | --json pipe | ✅ |
| Pi | — | JSONL tail | — | ✅ |
| Hermes | — | SQLite mtime | — | ✅ |
| OpenCode | — | SQLite mtime | SSE /sse | ✅ |

---

## The Bidirectional Loop

Every harness has two connections to Syke:

**Inbound (Observe)**: Connector skill configures transport → adapter compiles to canonical events → ledger. Deterministic. No LLM.

**Outbound (Distribute)**: Context skill injected into harness → agent gets memory at session start. The memex — synthesized from ALL harness events — flows back. Each harness benefits from observations across ALL harnesses.

The loop is complete when both skills are installed and healthy. Completion is measured per-harness, not globally.

---

## Federation In One Paragraph

Observe federates many harnesses into one immutable timeline.

That means:

- one canonical ledger
- many adapters and descriptors
- strong provenance on every event
- no capture-time conflict resolution
- no cross-harness linking at Observe time

If Claude Code, Codex, and another harness all describe the same underlying work differently, Observe stores all of it. Synthesis and later reasoning decide what matters. Capture does not pick winners.

---

## Compilation Path

All transports feed the same compile path:

1. Capture notification (hook payload, file change, poll discovery)
2. Targeted adapter ingest — reparse only the changed artifact/session
3. ContentFilter — credential sanitization with auditable redaction marker
4. Canonical event insertion — typed columns, external_id dedup, atomic per session
5. 5 event types: session.start, turn, tool_call, tool_result, ingest.error

The adapter contract: `discover()` finds artifacts, `iter_sessions()` compiles them to ObservedSession objects, the base class handles everything else. Three adapter shapes cover all formats:

- **parse_line()** — stateless per-line JSONL parser, for formats where each line carries all its own metadata (e.g. claude-code). Factory-generated with closed-loop feedback.
- **ObserveAdapter (JSONL)** — reads entire JSONL files, groups correlated events by turn, merges metadata from different lines (e.g. codex, where model/tokens/content are on separate lines).
- **ObserveAdapter (SQLite)** — queries SQLite databases, joins sessions/messages/parts tables (e.g. hermes, opencode).

All three shapes produce ObservedSession → ObservedTurn → Event through the same base class pipeline.

---

## Intelligence Bridge

Observe doesn't do intelligence. It feeds it:

- New events → dirty marker in ledger
- Debounced synthesis trigger (short quiet window, not 15-minute tick)
- Synthesis agent reads new events → updates memex
- Updated memex → refresh context skills in all harnesses

The ledger write is immediate. The synthesis is debounced. The distribution follows synthesis.

---

## What's Stable In 0.5

| Component | Status | Files |
|---|---|---|
| ObserveAdapter ABC | ✅ | `observe.py` |
| File/sqlite watch runtime | ✅ | `watcher.py`, `handler.py`, `sqlite_watcher.py`, `writer.py` |
| Harness descriptors + registry | ✅ | `descriptor.py`, `harness_registry.py`, `descriptors/*.toml` |
| Dynamic adapter resolution | ✅ | `adapter_registry.py` |
| Adapter factory/healing loop | ✅ | `factory.py` |
| Self-observation traces | ✅ | `trace.py` |
| sync.py integration | ✅ | `sync.py` |

---

## What Is Still Experimental

Observe is still evolving when it comes to breadth and automation. The open work is:

1. Better adapter coverage across harnesses
2. Stronger self-heal and drift recovery loops
3. Cleaner realtime observe service boundaries
4. Better synthesis trigger and eval integration
5. Tighter distribution symmetry across harnesses

---

*Document version: observe-phase3*
*Tests passing: 460*
*Events ingested: 151,669*
