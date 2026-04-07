# Current State

This is the baseline document for architecture discussions after the Pi-native migration.

Use it to answer three questions quickly:

- what Syke is now
- what survived from pre-migration Syke
- which older concepts should be mentally remapped instead of taken literally

For deeper detail, read [ARCHITECTURE.md](ARCHITECTURE.md), [RUNTIME_AND_REPLAY.md](RUNTIME_AND_REPLAY.md), and [../FOUNDATIONS.md](../FOUNDATIONS.md).

---

## One Sentence

Syke is a local-first memory runtime for the user and their agents. Pi is the embedded agent execution engine inside the Syke runtime, not the product boundary.

---

## The Current Contract

The current invariant is:

- `syke.db` = authoritative mutable learned-memory state (memories, links, memory_ops, synthesis_cursor, cycle_records, cycle_annotations, memories_fts)
- adapter markdowns at `~/.syke/data/{user}/adapters/{source}/adapter.md` = how the agent reads each harness
- `MEMEX.md` = routed workspace projection of current memory state, indexed by synthesis cycle numbers
- Pi workspace = execution surface for the Syke runtime
- exported memex artifacts and registered Syke capability files = distribution sinks

That means:

- truth of what happened lives in harness data at the source (JSONL, SQLite, JSON)
- truth of what Syke currently believes lives in `syke.db`
- the agent reads harness data directly via adapter markdowns + bash/sqlite3
- workspace files are runtime artifacts, not the source of truth

---

## What Survived The Migration

The Pi-native migration changed the runtime shape, but it did not change the product thesis.

These parts of Syke are still core:

- **Adapter markdowns**: describe each harness's data format and location so the agent can read directly
- **Memory**: agent-written mutable memories and links in syke.db
- **Memex**: the navigational timeline that routes humans and agents through the memory layer, indexed by cycle numbers
- **Distribution**: sending derived context into harness-native surfaces
- **Ask / Sync / Daemon / Replay**: the main product workflows
- **Self-observation**: Syke recording how its own runtime behaves
- **Local-first portability**: user-owned storage, no cloud dependency for the memory substrate itself

The thesis is unchanged:

- memory is identity
- evidence and inference must stay separate
- the system should get better by maintaining a user-specific learned map over time

---

## What Changed In The Pi-Native Migration

The runtime model was simplified hard.

Before, Syke still carried legacy ideas from the Claude SDK / LiteLLM period:

- a swappable runtime story
- runtime selection surfaces in config
- translator/proxy layers on the hot path
- older shims that made Pi look like one backend among many
- looser workspace authority assumptions

Now the operational model is:

- Pi is the only execution engine
- `syke.llm.pi_runtime` is the runtime routing surface
- provider selection is separate from runtime selection
- `config.toml` does not choose the runtime
- `ask`, `sync`, daemon cycles, and replay all use the same workspace contract
- workspace `syke.db` binds to the exact caller-owned canonical store

This is the main mental shift:

- old model: Syke called an agent backend
- current model: Syke is the runtime and memory product, and Pi is the execution engine inside it

---

## Authority Map

When discussing architecture, use this authority order:

### 1. Harness data authority

Harness data stays at the source (JSONL, SQLite, JSON files). The agent reads it directly via adapter markdowns installed at `~/.syke/data/{user}/adapters/{source}/adapter.md`. Each adapter directory also has agent-written `notes.md` and `cursor.md`.

### 2. Learned-state authority

`syke.db` is the authoritative mutable state store.

Today it holds:

- `memories`
- `links`
- memex state, currently represented through the memory layer
- `synthesis_cursor`
- `cycle_records`
- `cycle_annotations`
- `memory_ops`
- `memories_fts` (FTS5 index)

### 3. Workspace projection

`MEMEX.md` is the routed memex artifact inside the Pi workspace, indexed by synthesis cycle numbers. 190+ cycles are tracked in cycle_records.

It exists so Pi and external harnesses can consume a file-native projection of the current state, but it is not the canonical store.

### 4. Distribution sinks

Files such as the exported `MEMEX.md` and registered Syke capability outputs are downstream projections only.

---

## Runtime Flow

### `syke ask`

1. open the caller's `SykeDB`
2. bind workspace `syke.db`
3. ground the run from current memory state + MEMEX + adapter markdowns
4. run Pi in a fresh session (agent reads harness data directly via bash/sqlite3)
5. return answer plus runtime telemetry

The ask path works with: MEMEX + adapter markdowns + bash/sqlite3.

### `syke sync`

1. ensure adapter markdowns are installed for active harnesses
2. run Pi synthesis (agent reads harness data directly)
3. sync `MEMEX.md` back into the authoritative store
4. advance the synthesis cursor
5. distribute the current memex outward

### Daemon

The daemon orchestrates: scheduling, IPC, distribution. Each cycle calls `_reconcile()` (now a no-op since the old copy pipeline is gone), `_synthesize()`, and `_distribute()`. It can keep the Pi process warm on supported surfaces.

### Replay

Replay uses the same runtime contract, but against an isolated run-local `syke.db`.

---

## Validated Runtime State

The v2 architecture has been validated locally in both controlled and real-user runs.

What is now proven:

- no watcher, no copy pipeline, no events.db staging — the agent reads harness data directly
- host-shell `ask`, `context`, and `observe` all work against the current runtime shape
- the daemon idles correctly between synthesis cycles
- 190+ synthesis cycles tracked in cycle_records demonstrate the MEMEX-as-timeline model works

What this means operationally:

- the old SenseWriter/SenseWatcher/SQLiteWatcher infrastructure is fully removed
- no events.db snapshot to refresh, no workspace copy to maintain
- daemon CPU usage is minimal between synthesis cycles

---

## Observe Surface Now

Observe is now the adapter markdown installation surface, not a runtime ingest boundary.

Its job is:

- ship adapter markdown guides that describe each harness's data format and location
- install those guides at `~/.syke/data/{user}/adapters/{source}/adapter.md`
- create empty `notes.md` and `cursor.md` stubs for agent-written state

The agent reads harness data directly during synthesis and ask. There is no Python adapter ABC, no factory, no validator, no watcher runtime. The old copy pipeline (SenseWriter, SenseWatcher, SQLiteWatcher, sync_source, run_sync) has been fully removed.

That means:

- Observe = adapter markdown installation
- synthesis = agent reads harness data via adapter guides + bash/sqlite3, writes learned state
- ask = online navigation over learned state plus direct harness reads
- distribution = projecting learned state back into harness-native surfaces

`syke setup` and `syke sync` call `ensure_adapters()` to install adapter markdowns before synthesis runs. Seed adapter markdowns are shipped in `syke/observe/seeds/` (e.g., `adapter-claude-code.md`, `adapter-cursor.md`).

---

## Self-Observation Loop

Syke now observes itself as part of the same memory system.

Examples:

- `ask.start`
- `ask.complete`
- `ask.tool_use`
- `synthesis.start`
- `synthesis.complete`
- daemon and sense lifecycle events

These are written back as `source='syke'` telemetry events in `syke.db`.

That means the runtime loop is not only:

- harnesses -> adapter markdowns -> agent reads directly

It is also:

- Syke runtime behavior -> self-observation -> syke.db

So synthesis can eventually reason over both:

- what the user and external agents did
- what Syke itself did while helping them

This is the self-observing part of the architecture.

---

## Sandbox Model

The current model is:

- ask and synthesis run inside the Syke-controlled Pi workspace sandbox
- the agent has read access to harness data directories and read/write access to syke.db
- external agent sandboxes such as Codex, Claude Code, or other harness restrictions still exist, but they are outside Syke's control

Inside Syke itself there is one primary agent-execution sandbox boundary: the Pi workspace sandbox.

External harness sandboxes are downstream environment constraints, not part of Syke's internal runtime design.

---

## What Still Matters From Older Syke

Some older concepts still matter, but their meaning changed.

### Memex

Still central.

What changed:

- it is no longer best understood as a file like `CLAUDE.md`
- it is a product artifact whose authoritative state lives in `syke.db`
- `MEMEX.md` is the workspace projection of that state

### Memory Graph

Still real.

`memories` and `links` remain the current learned-memory substrate. The graph idea survived the migration completely.

### Observe / Adapter Markdowns

Evolved. The old Python adapter ingest pipeline is gone. Observe now means: adapter markdowns that describe harness data formats, installed at setup time. The agent reads harness data directly. Intelligence still belongs after the observed boundary.

### Distribution

Still core.

What changed:

- Pi now consumes the local workspace directly
- external harnesses consume routed projections
- the product boundary is not any one harness file

### Self-observation

Still core.

Syke still records runtime behavior, cycle outcomes, and operational signals so it can evaluate itself as a living system rather than just a CLI wrapper.

---

## Legacy Term Map

Use this map when reading older notes or talking through older decisions.

| Old framing | Current framing |
|---|---|
| runtime switch | Pi-only `syke.llm.pi_runtime` routing |
| Claude SDK backend | removed from the runtime path |
| LiteLLM proxy layer | removed from the runtime path |
| events.db evidence ledger | removed; agent reads harness data directly via adapter markdowns |
| ObserveAdapter ABC / Python adapters | removed; adapter markdowns describe format, agent uses bash/sqlite3 |
| SenseWriter / SenseWatcher / SQLiteWatcher | removed; no watchers, no copy pipeline |
| sync_source / run_sync / workspace snapshot | removed; no events.db copy, no staging |
| one mixed DB | `syke.db` only (memories, links, memory_ops, cycle_records, etc.) |
| memex as exported file | memex state in `syke.db`, projected to `MEMEX.md`, indexed by cycle numbers |
| ask agent vs synthesis agent as separate runtime concepts | same Syke runtime contract, different orchestration and grounding |
| Pi as JSON-RPC wrapper | Pi as a full agent runtime with sessions, workspace, and telemetry |
| perception | historical precursor; current system is adapter markdowns + memory + memex identity |

---

## Current Open Architectural Work

The migration is complete enough to ask architecture questions cleanly, but a few design improvements remain open.

These are not baseline confusions anymore. They are next-order architecture work:

- synthesis commit atomicity
- first-class memex storage inside `syke.db`
- sharper conceptual split between learned state and runtime/audit state inside `syke.db`
- deeper use of Pi session lineage and runtime-native controls

The most important current open loop is synthesis commit boundary:

- today a cycle can mutate learned state, then sync memex, then advance cursor in separate steps
- the target shape is one coherent cycle commit model

---

## Reading Order

If you want the cleanest way to reason about Syke now:

1. read this document first
2. read [ARCHITECTURE.md](ARCHITECTURE.md) for the system model
3. read [RUNTIME_AND_REPLAY.md](RUNTIME_AND_REPLAY.md) for the execution contract
4. read [../FOUNDATIONS.md](../FOUNDATIONS.md) for thesis, principles, and research direction

Use `FOUNDATIONS.md` as the vision and principles document.
Use this file and the runtime docs as the source for current implementation reality.
