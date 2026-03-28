# Syke Memory Architecture

> How Syke builds a living, self-evolving model of who you are.

---

## Design Philosophy

Memory is not search. Syke is not trying to be a generic retrieval layer. It is agentic memory: a system that observes activity across many harnesses, preserves evidence in an immutable timeline, and maintains a memex that routes future agents through that evidence.

**What makes this different:**

**Memory is identity, not retrieval.** Most memory systems are glorified search engines — ingest data, embed it, retrieve it. Syke's thesis is that memory IS the user's computational identity. The memex doesn't just answer questions about what happened — it reflects who this person is, what they care about, how they think. The system evolves its own understanding rather than waiting to be queried.

**User-owned, federated, portable.** Two user-owned SQLite stores per user, plus a local Pi workspace derived from them: `events.db` is the immutable evidence ledger and `syke.db` is the mutable learned-memory store. No cloud dependency, no vendor lock-in. Copy the user data directory, move it anywhere. The user owns their memory — Syke is the harness, not the host.

**Dynamic and self-evolving.** The observed timeline is immutable. The memex is mutable. The synthesis loop decides how the memex should change as new evidence arrives. The exact synthesis contract is still being refined through experiments in the 0.5 branch.

**Designed for the agentic era.** AI tools are becoming the primary interface for knowledge work. Syke is built for a world where multiple AI agents operate on a user's behalf and each needs context. The memex becomes a shared dashboard — highly relevant for agentic crawling, health checks, personalization, and cross-tool coordination.

**Reflects implicit ontology.** Every person has a unique mental model — how they organize projects, what they prioritize, how they communicate. Traditional software imposes a fixed schema. Syke lets the agent discover the user's ontology from their usage patterns. This is why everyone wants their perfect todo app and can't have it — because software isn't generative yet. Syke is a step toward personalized ontology, where the system adapts to the user rather than the user adapting to the system.

**Memory is maintenance.** Beyond store and retrieve, memory needs active care: synthesis cycles, cron-driven updates, health checks, evolution tracking. This is why agentic memory requires an agent — not just a database with an API, but an autonomous process that maintains, curates, and evolves the knowledge base.

**Core principles:**
- **Observe runtime is pure capture** — no LLM in the ingest boundary. Read harness data, parse mechanically, store append-only events. Intelligence belongs after the observed boundary.
- **Per-turn events** — each user intent → agent response is one event (1-5KB), not one 50KB session blob. Session grouping via session_id column.
- **Evidence ≠ inference** — raw events (what happened) are immutable; memories (what it means) are mutable and agent-written
- **The agent crawls text** — FTS5/BM25 for retrieval, LLM for understanding. No vector DB needed.
- **Graph over SQLite** — memories connect through sparse, bidirectional links with natural language reasons
- **The map appears** — the agent builds its own world model with each use, like fog of war clearing
- **Failures are telemetry** — parse errors, unknown schemas, adapter mismatches are stored as anomaly events, not silently dropped

```
┌─────────────────────────────────────────────────────────┐
│              Layer 1: Evidence Ledger                    │
│              ┌──────────────────────┐                    │
│              │  SQLite + WAL + FTS5 │                    │
│              └──────────┬───────────┘                    │
│                         │ synthesis extracts             │
├─────────────────────────┼───────────────────────────────┤
│              Layer 2: Memories + Graph                   │
│                         │                                │
│         ┌───────────────▼───────────────┐                │
│         │          Memories             │                │
│         │   (free-form text, agent-     │                │
│         │    written, FTS5-indexed)     │                │
│         └───────┬───────────┬───────────┘                │
│                 │           │                            │
│          ┌──────▼──────┐    │                            │
│          │    Links    │    │                            │
│          │  (sparse,   │    │                            │
│          │  bidirect., │    │                            │
│          │  NL reasons)│    │                            │
│          └─────────────┘    │                            │
│                             │ agent rewrites             │
├─────────────────────────────┼───────────────────────────┤
│              Layer 3: Memex (The Map)                    │
│         ┌───────────────▼───────────────┐                │
│         │  Navigational index of who    │                │
│         │  this person is. Routes to    │                │
│         │  memories, not a report.      │                │
│         └───────────────────────────────┘                │
├─────────────────────────────────────────────────────────┤
│              Layer 4: Memory Ops                         │
│         ┌───────────────────────────────┐                │
│         │  Audit trail + training data  │                │
│         │  Every op logged: create,     │                │
│         │  update, supersede, link      │                │
│         └───────────────────────────────┘                │
└─────────────────────────────────────────────────────────┘
```

---

## Layer Architecture

### Layer 1: Evidence Ledger

Append-only event store. Immutable, timestamped, source-tagged.

```
events table (SQLite + WAL + FTS5)
├── id: UUID7
├── user_id: string
├── source: "claude-code" | "github" | "chatgpt" | "gmail" | "mcp-record"
├── timestamp: ISO 8601
├── event_type: "session.start" | "turn" | "session" | "commit" | ...
├── title: string
├── content: text (full turn content — no cap for Observe events)
├── metadata: JSON (source-specific: role, turn_index, tools_used, ...)
├── external_id: dedup key ("claude-code:{session_id}:turn:{idx}")
├── session_id: groups turns within a session (nullable)
└── parent_session_id: links subagent sessions to parent (nullable)
```

Events are never modified. This is the ground truth — everything else is derived.

### Observe Harness And Factory

The Observe Harness is the deterministic ingest boundary for the whole system.

It is responsible for:

- discovering harness artifacts
- parsing them through adapter code
- normalizing them into canonical events
- appending them into `events.db`

The factory is the control plane for Observe, not the runtime brain. It exists to generate, test, deploy, and heal adapters as harness formats evolve.

That means:

- Observe = sensory boundary
- factory = adapter scaffolding and healing
- neither of them is the synthesis engine

This is the self-scaffolding side of Syke: the system can evolve or repair its own ingest layer without collapsing the trusted capture boundary into the agent runtime.

Operationally, the JSONL watcher keeps warm restart state in `observe_watchers.json` next to the user DB. That state stores per-file checkpoints so daemon restart does not rewalk or retail the whole corpus every time. On startup, the watcher now:

- skips known files whose persisted checkpoint still matches the current file
- bootstraps only files that are new, grown, truncated, or inode-replaced
- seeds size state for skipped files so the first unchanged filesystem event does not retrigger work

One important boundary: startup bootstrap is a watcher resume path, not the authoritative ingest path for historical JSONL contents. On macOS, an unknown file at startup is checkpointed and marks the source dirty for reconcile; the source adapter remains the authoritative path for full historical ingest into `events.db`.

### Layer 2: Memex

The memex is the current mutable routing layer. It is one agent-managed artifact that gives both humans and agents orientation: what exists, what is active, what changed, and where deeper evidence lives.

The memex is currently stored in the main Syke DB and projected into the Pi workspace as `MEMEX.md`. Product-wise it should be understood as the primary mutable artifact, not as one memory among many.

The important point in 0.5 is not a fixed named tool contract. It is that the Pi runtime receives the Syke workspace contract and can:

- inspect immutable evidence through `events.db`
- update mutable learned state in `syke.db`
- rewrite `MEMEX.md`
- persist session artifacts and helper scripts inside the workspace

`syke ask` and synthesis now both route through the same Pi runtime. The difference is grounding and orchestration, not a separate non-Pi backend.

### Layer 3: Distribution

The memex is rendered back into agent environments. The authoritative mutable state lives in `syke.db`, and `MEMEX.md` is the routed workspace projection of that state. Harness-specific projections such as `CLAUDE.md` or installed `SKILL.md` files are distribution sinks, not the product boundary.

```markdown
# Memex — {user}

## What's Happening Now (stable entities)
[mem_xxx] Project Name — one-line status
[mem_yyy] Person — relationship context

## Patterns & Threads
Topic → search 'keyword' or query linked memories for mem_xxx
Recent → query events since last_week

## Context
Sources: claude-code, github, chatgpt. N events. Last sync: date.
```

The memex is NOT a report — it's a map. The agent reads this first, then navigates. It self-organizes based on what's actually important to this person — no prescribed structure. Over time, it becomes a shared dashboard between the human and their AI agents — a live view of what matters, what's moving, and where to look.

Current 0.5 distribution is intentionally simple:

- trusted Syke owns the live store, auth, and metrics
- Pi consumes the local workspace contract directly
- external agent environments consume exported views of the memex
- `syke context` is the most reliable read surface
- `syke ask` is deeper, but some external sandboxes cannot open the live store directly yet

So the current operational boundary is not "every sandbox can query the DB." It is "every sandbox should at least receive the memex, and trusted Syke can answer deeper questions when direct access is available."

### Layer 4: Cycle Records And Audit

Every synthesis cycle is logged with timing, cost, tokens, and outcome. Self-observation events and experiment artifacts then provide the substrate for later eval and prompt iteration.

Self-observation is part of the same evidence system, not a separate analytics plane. Runtime events such as ask lifecycle, synthesis lifecycle, daemon events, and tool observations are written back as `source='syke'` events so the system can reason over its own behavior as well as user and harness activity.

---

## Runtime Boundary: Pi And Syke

Syke now treats Pi as the canonical agent runtime, not as a swappable stateless backend.

Pi is responsible for runtime concerns:

- agent execution and tool orchestration
- session lifecycle and session persistence
- provider/model execution after Syke prepares config and workspace
- runtime event streaming, retries, compaction, and runtime exports
- enforcing the Syke-controlled workspace sandbox during ask and synthesis

Syke is responsible for memory-product concerns:

- ingesting and normalizing evidence into the append-only ledger
- defining the workspace contract and refreshing `events.db`, `syke.db`, and `MEMEX.md`
- deciding synthesis policy, ask grounding, and replay semantics
- tracking product metrics, self-observation, and harness distribution
- keeping Observe and factory on the trusted side of the intelligence boundary

This is the practical split:

- Pi owns how the agent runs
- Syke owns what the agent knows, what sources it can inspect, and how those results become durable memory

## Sandbox Boundary

Syke now has one primary internal agent sandbox boundary: the Pi workspace sandbox.

That sandbox applies to ask and synthesis. It controls:

- filesystem access within the workspace
- read-only protection for `events.db`
- denial of credential paths and secret files
- network policy for provider access

Observe and factory are intentionally outside that sandbox. They are trusted local code operating before the inference boundary and should stay deterministic.

External harness sandboxes still exist, but they are downstream environment constraints rather than part of Syke's internal runtime model. In practice:

- internal Syke sandbox = Pi workspace sandbox
- external harness sandboxes = consumers of memex/distribution that may or may not reach the live store

---

## Graph over SQLite

Human memory is associative. You don't retrieve memories by index — you follow connections. A project reminds you of a person, who reminds you of a conversation, which connects to a decision. Syke models this with explicit links — sparse, bidirectional edges with natural language reasons, implemented over SQLite.

```
┌──────────┐         ┌──────────────────────────┐         ┌──────────┐
│  EVENTS  │         │        MEMORIES          │         │  MEMEX   │
│──────────│ synth   │──────────────────────────│ routes  │──────────│
│ id       │────────►│ id                       │────────►│ id       │
│ source   │ extracts│ content (agent-written)  │   to    │ content  │
│ content  │         │ source_event_ids → [EVT] │         │ (the map)│
│ timestamp│         │ active                   │         └──────────┘
└──────────┘         └─────┬──────────┬─────────┘
                           │          │
                           │  ┌───────▼────────┐
                           │  │     LINKS      │
                           │  │────────────────│
                           │  │ source_id ──►  │
                           └──│ target_id ──►  │
                              │ reason (NL)    │
                              └────────────────┘

        Bidirectional: agent queries both directions via SQL.
        Sparse: 3-5 links per memory, not hundreds.
```

The agent creates links during synthesis via `sqlite3` INSERT and navigates them during ask via SQL queries. Links are bidirectional — the agent queries both directions, returning connected memories with their reasons.

### Why This Works

The [MEMEX_EVOLUTION](MEMEX_EVOLUTION.md) experiment proved that even without explicit graph infrastructure — just agent context engineering (ACE) — the synthesis agent invented pointers on its own under budget pressure. It compressed its memex from inline detail to `→ Memory: {id}` references, discovering indirection as a compression strategy. When the pointer instruction was removed entirely, the agent crashed, recovered, and invented pointers anyway.

The links table makes this emergent pattern first-class. Instead of relying on emergence alone, the agent has explicit tools to create and traverse connections. The graph structure that the agent discovered naturally now has infrastructure to support it.

### Why Not a Graph Database

The graph is sparse — 3-5 links per memory, not hundreds. Two indexed columns (`source_id`, `target_id`) and a JOIN handle bidirectional traversal. Graph databases solve dense traversal problems Syke doesn't have. And the graph lives in the same SQLite file as everything else — one portable file, not two services.

### Why free-form text over structured schemas?

The agent organizes knowledge the way it naturally thinks — in prose, markdown, lists, whatever fits. A memory about movie preferences might have categories like "with gf", "period films", "comfort watches" — organic structure that emerges from use, not imposed by schema.

### Why supersession over versioning?

When knowledge changes significantly, the old memory is deactivated and a new one takes its place. The chain is preserved: querying the `superseded_by` column walks the supersession links. This is simpler than version control and matches how human memory works — you don't version your beliefs, you update them.

### Why a separate memex?

Without a map, the agent would need to search blindly every time. The memex gives it orientation — what exists, where to look, what's currently important. It's the difference between exploring a city with and without a map.

---

## Inspiration

Syke's memory architecture draws from several research directions:

**[ACE — Agentic Context Engineering](https://arxiv.org/abs/2510.04618)** (Zhang et al. — Stanford/Microsoft, ICLR 2026): Treats contexts as evolving playbooks that accumulate, refine, and organize strategies through generation, reflection, and curation. Syke's synthesis loop is an ACE implementation — the memex is a playbook that evolves with each cycle, accumulating the user's strategies and knowledge rather than summarizing them away. The MEMEX_EVOLUTION experiment is direct evidence of ACE dynamics: the agent developed its own compression and routing strategies under budget pressure.

**[RLM — Recursive Language Models](https://arxiv.org/abs/2512.24601)** (Zhang, Kraska, Khattab — MIT CSAIL, Dec 2025): Treats long prompts as an external environment the LLM programmatically examines, decomposes, and recursively calls itself over. Syke borrows the core idea: memory lives outside the context window, and the agent navigates it via tools rather than stuffing everything into the prompt.

**[ALMA — Automated Meta-Learning of Memory designs for Agentic systems](https://arxiv.org/abs/2602.07755)** (Xiong, Hu, Clune — Feb 2026): A Meta Agent searches over memory designs (database schemas, retrieval and update mechanisms) expressed as executable code, outperforming hand-crafted designs by 6-12 points. Syke's takeaway: design around a pluggable `update()`/`retrieve()` protocol so the memory architecture can evolve without rewriting the agent.

**[LCM — Lossless Context Management](https://papers.voltropy.com/LCM)** (Ehrlich, Blackman — Voltropy, Feb 2026): Decomposes RLM-style recursion into deterministic, engine-managed primitives — a DAG-based hierarchical summary system that compacts older messages while retaining lossless pointers to originals. Syke's takeaway: hierarchical compression where recent context stays full, older context compacts, and nothing is truly lost.

**[Persona](https://github.com/saxenauts/persona)** (Saxena, 2025 — private repo): Syke's predecessor. Explored graph-vector hybrid retrieval, PersonaMem benchmarks, and BEAM evaluation for knowledge-grounded memory. Key lessons carried forward: graph traversal for associative navigation, the 4-pillar memory model, and the insight that design heuristics hit a ceiling — agentic approaches are needed.

**Syke-native**: Session atomicity, evidence ≠ inference, sparse links, agent crawls text, portable SQLite, the map appears bottom-up from exploration.

---

## File Map

```
syke/
├── cli.py                      # Click CLI command surface
├── config.py                   # Runtime constants + env/config resolution
├── config_file.py              # Typed TOML schema + parser + default template
├── db.py                       # SQLite + WAL + FTS5, events + memex + cycle records
├── models.py                   # Event and memory-layer models
├── sync.py                     # Ingest -> synthesize -> distribute orchestration
├── daemon/
│   ├── daemon.py               # Background observe/synthesize/distribute loop
│   └── metrics.py              # Daemon metrics/logging helpers
├── distribution/
│   ├── context_files.py        # Render memex into current file targets
│   └── harness/                # Distribution adapters for external harnesses
│       ├── base.py             # HarnessAdapter ABC + status/result types
│       ├── claude_desktop.py   # Claude Desktop trusted folders adapter
│       └── hermes.py           # Hermes adapter
├── llm/                        # Provider registry + auth + Pi runtime wiring
│   ├── pi_runtime.py           # Pi-native ask/synthesis dispatcher
│   ├── backends/               # Canonical backend implementations
│   │   ├── pi_ask.py           # Pi ask() agent
│   │   └── pi_synthesis.py     # Pi synthesis agent
│   ├── pi_client.py            # Pi RPC client + singleton runtime lifecycle
│   ├── providers.py            # Provider specs (all providers)
│   ├── auth_store.py           # Auth store at ~/.syke/auth.json
│   ├── env.py                  # Provider resolution + Pi env construction
│   ├── pi_settings.py          # Workspace-local .pi/settings.json generation
│   ├── codex_auth.py           # Codex token reader (~/.codex/auth.json)
│   └── codex_proxy.py          # Codex model/helper bridge
├── observe/                    # Deterministic observation runtime + adapter factory
│   ├── observe.py              # ObserveAdapter base + canonical event extraction
│   ├── handler.py              # File event routing
│   ├── watcher.py              # File watch runtime
│   ├── tailer.py               # JSONL tailing with offset tracking
│   ├── writer.py               # Threaded batch event writer
│   ├── sqlite_watcher.py       # SQLite watch runtime
│   ├── trace.py                # System telemetry (source='syke' events)
│   ├── descriptor.py           # TOML harness descriptor loader
│   ├── harness_registry.py     # Descriptor registry + health checks
│   ├── adapter_registry.py     # Runtime + dynamic adapter resolution
│   ├── dynamic_adapter.py      # Wrap generated parse logic as ObserveAdapter
│   ├── factory.py              # Generate/test/deploy/heal control plane
│   └── descriptors/            # Harness descriptors
├── memory/
│   ├── memex.py                # Memex read/write/bootstrap
│   └── skills/
│       └── pi_synthesis.md     # Pi synthesis skill copy for memory surfaces
└── runtime/
    ├── __init__.py             # PiRuntime singleton lifecycle management
    ├── workspace.py            # Workspace setup, validation, DB refresh
    ├── sandbox.py              # Sandbox config for Pi network/permissions
    └── agents_md.py            # Minimal AGENTS.md bootstrap rendering
```

---

## Current Runtime Notes

- **0.5 branch** is still under active architecture and synthesis experimentation
- **Pi-only runtime** for ask and synthesis
- **workspace contract** = `events.db`, `syke.db`, `MEMEX.md`, `sessions/`, `scripts/`, minimal `AGENTS.md`
- **SQLite + FTS5** for storage and retrieval (FTS5 sync via triggers)
- **macOS-first daemon workflow** today

---

## Agent Runtime Architecture

Syke now supports one agent runtime for synthesis and ask operations: Pi. `pi_runtime.py` is the dispatcher used by the CLI and routes directly to Pi implementations.

### Pi Runtime Dispatcher (`syke/llm/pi_runtime.py`)

The Pi dispatcher is the routing layer:

```
CLI / Sync / Daemon / Replay
        ↓
   pi_runtime.run_ask()
   pi_runtime.run_synthesis()
        ↓
     Pi Runtime
```

All callers should treat `pi_runtime` as the import path for ask/synthesis dispatch.

### Pi Runtime (Canonical)

- **Implementation**: `syke/llm/backends/pi_ask.py`, `syke/llm/backends/pi_synthesis.py`
- **Runtime**: Pi RPC subprocess (`syke/llm/pi_client.py`) — singleton lifecycle in `syke/runtime/`
- **Workspace**: Persistent `~/.syke/workspace` with readonly `events.db`, writable `syke.db`, routed `MEMEX.md`, session artifacts, helper scripts, and minimal `AGENTS.md`
- **Tools**: Pi's built-in runtime tool surface
- **Metrics**: Pi-native duration, provider/model, token, cache, cost, and tool-call telemetry
- **Best for**: The normal Syke runtime path

---

## Harness Adapter System

Syke distributes memory context to other AI agents via harness adapters. Each adapter handles one platform:

```
HarnessAdapter (ABC)
├── detect()     → Is this platform installed?
├── install()    → Write Syke context (SKILL.md, config, etc.)
├── status()     → Health check: detected + connected?
└── uninstall()  → Clean removal

Protocol metadata:
  name, display_name, protocol ("agentskills"/"json-config"/...), 
  protocol_version, has_native_memory
```

**Design**: A/B test mode by default — Syke coexists with native memory, never replaces it. Adapters declare their protocol and version, isolating format changes per-adapter. Registry auto-discovers adapters; `install_all()` runs during setup and daemon refresh.

Community adapter requests tracked at [GitHub #8](https://github.com/saxenauts/syke/issues/8).

---

## LLM Provider Layer

Syke uses Pi as the canonical agent runtime and translates Syke provider config into Pi-native provider settings plus environment variables.

```
                    ┌────────────────────┐
                    │   Pi Coding Agent  │
                    │  RPC + workspace   │
                    └─────────┬──────────┘
                              │
            ┌─────────────────┼──────────────────┐
            ▼                 ▼                  ▼
     Built-in Pi        Pi OAuth/Auth      Workspace Extensions
      providers            surfaces        (OpenAI-compatible)

  openrouter            codex              ollama
  zai                   anthropic*         vllm
  kimi-coding                               llama-cpp
  openai
  azure-openai-responses
```

### Environment Isolation

`clean_claude_env()` still strips inherited Claude markers from the parent shell so Pi subprocesses do not pick up stale auth or nesting env by accident. Pi-native provider env is built by `syke/llm/env.py`, and workspace-local `.pi/settings.json` is generated by `syke/runtime/pi_settings.py`.

### Auth & Config

Credentials stored in `~/.syke/auth.json` (managed by `syke auth set`). Non-secret provider settings in `~/.syke/config.toml` under `[providers.<name>]`. See `docs/CONFIG_REFERENCE.md` for the full setting catalog.
