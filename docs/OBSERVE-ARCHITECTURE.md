# Observe Architecture

Observe is Syke's deterministic capture layer. It ingests activity from AI harnesses into a canonical event ledger. No LLM at capture time. Same input → same events. Time axis, append-only.

Observe has two concerns: **transport** (getting data to Syke in real-time) and **compilation** (parsing harness-native format into canonical events). Transport is fast and lossy-tolerant. Compilation is deterministic and lossless. Both feed the same ledger.

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

The **adapter-droid** maintainer skill creates, health-checks, and heals both the connector skill and the adapter/descriptor pair.

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

## Compilation Path

All transports feed the same compile path:

1. Capture notification (hook payload, file change, poll discovery)
2. Targeted adapter ingest — reparse only the changed artifact/session
3. ContentFilter — credential sanitization with auditable redaction marker
4. Canonical event insertion — typed columns, external_id dedup, atomic per session
5. 5 event types: session.start, turn, tool_call, tool_result, ingest.error

The adapter contract: `discover()` finds artifacts, `iter_sessions()` compiles them to ObservedSession objects, the base class handles everything else. Three adapter shapes cover all formats: JSONL readers, SQLite readers, JSON readers.

---

## Intelligence Bridge

Observe doesn't do intelligence. It feeds it:

- New events → dirty marker in ledger
- Debounced synthesis trigger (short quiet window, not 15-minute tick)
- Synthesis agent reads new events → updates memex
- Updated memex → refresh context skills in all harnesses

The ledger write is immediate. The synthesis is debounced. The distribution follows synthesis.

---

## What's Shipped

| Component | Status | Files |
|---|---|---|
| ObserveAdapter ABC | ✅ | observe.py (438 lines) |
| 5 working adapters | ✅ | claude_code.py, codex.py, pi.py, hermes.py, opencode.py |
| HarnessRegistry + 7 descriptors | ✅ | registry.py, descriptors/*.toml |
| ContentFilter (standalone) | ✅ | content_filter.py |
| sync.py via registry | ✅ | sync.py |
| Adapter connection skill | ✅ | docs/skills/adapter-connection.md |
| 460 tests | ✅ | tests/ |
| 151,669 events from real data | ✅ | Validated against 5 harnesses |

---

## What's Left (Completion Criteria)

Observe is complete when every supported harness has:

1. One real-time or near-real-time inbound path (hook or watch)
2. One poll recovery path (existing)
3. One installed outbound context skill
4. An agent-runnable health/heal loop
5. New events hit the ledger quickly (seconds, not minutes)
6. Downtime only affects freshness, not correctness

Concrete remaining work:

| Task | What | Status |
|---|---|---|
| `observe-rt` service | HTTP hook receiver + file watch supervisor | Not built |
| Connector skills | `syke-observe-<harness>/SKILL.md` for each harness | Not built |
| Context skill generalization | `syke-context/` for all harnesses (only Claude Code today) | Partial |
| Dirty/debounce trigger | Post-ingest signaling to synthesis | Not built |
| Adapter-droid formalization | Maintainer skill for install/check/heal/verify | Documented, not exercised |

---

*Document version: observe-phase3*
*Tests passing: 460*
*Events ingested: 151,669*
