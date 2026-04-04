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

- `events.db` = immutable evidence ledger
- `syke.db` = authoritative mutable learned-memory state
- `MEMEX.md` = routed workspace projection of current memory state
- Pi workspace = execution surface for the Syke runtime
- exported memex artifacts and registered Syke capability files = distribution sinks

That means:

- truth of what happened lives in `events.db`
- truth of what Syke currently believes lives in `syke.db`
- workspace files are runtime artifacts, not the source of truth

---

## What Survived The Migration

The Pi-native migration changed the runtime shape, but it did not change the product thesis.

These parts of Syke are still core:

- **Observe**: mechanical ingest of external activity into an append-only evidence ledger
- **Memory**: agent-written mutable memories and links over that evidence
- **Memex**: the navigational map that routes humans and agents through the memory layer
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

### 1. Evidence authority

`events.db` is the immutable ledger of observed external events.

Examples:

- tool activity
- turns and sessions
- GitHub activity
- self-observation events emitted by Syke itself

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

### 3. Workspace projection

`MEMEX.md` is the routed memex artifact inside the Pi workspace.

It exists so Pi and external harnesses can consume a file-native projection of the current state, but it is not the canonical store.

### 4. Distribution sinks

Files such as the exported `MEMEX.md` and registered Syke capability outputs are downstream projections only.

---

## Runtime Flow

### `syke ask`

1. open the caller's `SykeDB`
2. rebuild the workspace from that exact DB pair
3. refresh `events.db`
4. bind workspace `syke.db`
5. ground the run from current memory state in `syke.db`
6. run Pi in a fresh session
7. return answer plus runtime telemetry

Today the ask path rebuilds the DB-backed workspace contract before Pi runs. It does not depend on `MEMEX.md` being freshly rewritten on every ask invocation.

### `syke sync`

1. ingest new events
2. refresh the workspace contract
3. run Pi synthesis
4. sync `MEMEX.md` back into the authoritative store
5. advance the synthesis cursor
6. distribute the current memex outward

### Daemon

The daemon runs the same logical flow as `syke sync`, but can keep the Pi process warm on supported surfaces.

### Replay

Replay uses the same runtime contract, but against an isolated run-local `syke.db` and `events.db`.

---

## Validated Runtime State

The current tree has now been validated locally in both controlled and real-user runs.

What is now proven:

- the JSONL startup watcher no longer reprocesses the whole known corpus on warm restart
- a controlled 3,000-file JSONL corpus produced 3,000 `sense.file.detected` events across two daemon starts total, not 6,000
- the current repo daemon can return to idle on real-user data after startup and synthesis complete
- host-shell `ask`, `context`, and `observe` all work against the current runtime shape
- watcher-fed startup bursts now backpressure `SenseWriter` instead of dropping evidence in the validated burst/load cases

What this means operationally:

- the old heat loop from unconditional JSONL startup bootstrap is closed in the current tree
- if a currently installed background daemon still sits at ~100% CPU while Pi sleeps, that is an install/runtime-version problem, not evidence that the current tree still has the same watcher bug

One important live-watch gap was closed in the current tree:

- newly created JSONL files on macOS now read already-written initial contents even when the first live watchdog event is `modified` instead of `created`, as long as startup bootstrap already completed

So the current state is:

- warm restart behavior is materially better and validated
- idle daemon behavior is correct in the current repo run
- startup burst handling no longer loses evidence under the validated local burst/backpressure tests
- first-write delivery for newly created JSONL files on macOS is now covered in the live watcher path after startup bootstrap

---

## Observe Harness Now

The Observe Harness is now the deterministic sensor boundary of the Syke runtime.

Its job is:

- discover harness-native artifacts such as JSONL files, SQLite logs, or other local traces
- parse them mechanically through adapters
- normalize them into canonical `Event` rows
- append them into `events.db`

Observe does not run Pi and does not perform synthesis. It is intentionally pre-agent and pre-inference.

That means:

- Observe = trusted local capture
- synthesis = learned-state maintenance after capture
- ask = online navigation over learned state plus evidence
- distribution = projecting learned state back into harness-native surfaces

The adapter factory belongs to Observe, but it is not the runtime brain. Its role is to scaffold or heal adapters so Observe can keep capturing changing harness formats.

So the self-scaffolding part of Syke is:

- shipped seeds cover the known harness catalog
- setup validates and deploys those seeds first
- factory repairs or generates the ingest path only when the shipped seed is missing or no longer fits the detected local artifact shape
- Observe keeps turning harness activity into evidence
- synthesis turns evidence into learned memory
- distribution sends that learned state back out into harnesses

On the current branch, that scaffolding is no longer daemon-only. `syke setup` and `syke sync` now bootstrap Observe through the shipped-seed-first path before they try to ingest, so a clean install does not depend on preexisting user-local adapter artifacts and does not need to run the factory for normal known-harness setup.

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

These are written back as normal `source='syke'` events in the evidence ledger.

That means the runtime loop is no longer only:

- harnesses -> Observe -> evidence

It is also:

- Syke runtime behavior -> self-observation -> evidence

So synthesis can eventually reason over both:

- what the user and external agents did
- what Syke itself did while helping them

This is the self-observing part of the architecture.

---

## Sandbox Model

The migration did not collapse every sandbox in the ecosystem into one universal sandbox.

The current model is:

- Observe and the adapter factory run as trusted local Python code outside the Pi sandbox
- ask and synthesis run inside the Syke-controlled Pi workspace sandbox
- external agent sandboxes such as Codex, Claude Code, or other harness restrictions still exist, but they are outside Syke's control

So inside Syke itself there is one primary agent-execution sandbox boundary: the Pi workspace sandbox.

That sandbox is layered:

- workspace-scoped read/write rules from `.pi/sandbox.json`
- OS-level enforcement from Pi's runtime sandboxing support
- read-only `events.db` protection and blocked access to credentials/secrets

This is why the clean boundary is:

- trusted capture before the intelligence boundary
- sandboxed agent execution after the intelligence boundary

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

### Observe

Still foundational.

The ingest boundary remains deterministic and append-only. Intelligence still belongs after the observed boundary, not inside adapters.

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
| one mixed DB | split `events.db` + `syke.db` |
| memex as exported file | memex state in `syke.db`, projected to `MEMEX.md`, then distributed outward |
| ask agent vs synthesis agent as separate runtime concepts | same Syke runtime contract, different orchestration and grounding |
| Pi as JSON-RPC wrapper | Pi as a full agent runtime with sessions, workspace, and telemetry |
| perception | historical precursor; current system is observe + memory + memex identity |

---

## Current Open Architectural Work

The migration is complete enough to ask architecture questions cleanly, but a few design improvements remain open.

These are not baseline confusions anymore. They are next-order architecture work:

- synthesis commit atomicity
- first-class memex storage inside `syke.db`
- normalized evidence mappings instead of overloaded JSON provenance fields
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
