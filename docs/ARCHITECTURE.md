# Syke Memory Architecture

> How Syke builds a living, self-evolving model of who you are.

---

## Design Philosophy

Memory is not search. Syke is not trying to be a generic retrieval layer. It is agentic memory: a system that observes activity across many harnesses, preserves evidence in an immutable timeline, and maintains a memex that routes future agents through that evidence.

**What makes this different:**

**Memory is identity, not retrieval.** Most memory systems are glorified search engines — ingest data, embed it, retrieve it. Syke's thesis is that memory IS the user's computational identity. The memex doesn't just answer questions about what happened — it reflects who this person is, what they care about, how they think. The system evolves its own understanding rather than waiting to be queried.

**User-owned, federated, portable.** One SQLite file per user. No cloud dependency, no vendor lock-in. Copy the file, move it anywhere. The user owns their memory — Syke is the harness, not the host.

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

### Layer 2: Memex

The memex is the current mutable routing layer. It is one agent-managed artifact that gives both humans and agents orientation: what exists, what is active, what changed, and where deeper evidence lives.

The memex is currently stored using the memories table convention (`source_event_ids = ["__memex__"]`), but product-wise it should be understood as the primary mutable artifact, not as one memory among many.

**Synthesis agent** operates with 6 tools:
```
Bash, Read, Write, Grep, Glob             → filesystem + sqlite3 CLI for DB access
commit_cycle(status, content, hints)       → finalize synthesis cycle (only MCP tool)
```

The important point in 0.5 is not a rich memory tool surface. It is that the synthesis loop can inspect the observed timeline, rewrite the memex, and record the cycle cleanly.

**Ask agent** is a wrapper around synthesis — same sandbox, same skill file, same DB access. It has 3 tools:
```
Bash, Read, Grep                           → read-only filesystem + sqlite3 access
```

### Layer 3: Distribution

The memex is rendered back into agent environments. Today the concrete path is file and harness distribution, with `CLAUDE.md` as the primary current target. That render target is not the product boundary; the memex is.

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

### Layer 4: Cycle Records And Audit

Every synthesis cycle is logged with timing, cost, tokens, and outcome. Self-observation events and experiment artifacts then provide the substrate for later eval and prompt iteration.

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
│   ├── ask_agent.py            # ask() agent over memex + observed timeline
│   └── harness/                # Distribution adapters for external harnesses
│       ├── base.py             # HarnessAdapter ABC + status/result types
│       ├── claude_desktop.py   # Claude Desktop trusted folders adapter
│       └── hermes.py           # Hermes adapter
├── llm/                        # Provider registry + auth + env/proxy wiring
│   ├── providers.py            # Provider specs (all providers)
│   ├── auth_store.py           # Auth store at ~/.syke/auth.json
│   ├── env.py                  # Provider resolution + agent env construction
│   ├── litellm_config.py       # LiteLLM YAML config generation
│   ├── litellm_proxy.py        # LiteLLM proxy lifecycle (singleton)
│   ├── codex_auth.py           # Codex token reader (~/.codex/auth.json)
│   └── codex_proxy.py          # Codex translator proxy (Claude API ↔ OpenAI)
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
└── memory/
    ├── synthesis.py            # Synthesis agent + skill file loading + cycle records
    ├── memex.py                # Memex read/write/bootstrap
    └── skills/
        └── synthesis.md        # Skill file (control plane for synthesis agent)
```

---

## Current Runtime Notes

- **0.5 branch** is still under active architecture and synthesis experimentation
- **6 synthesis tools** (Bash, Read, Write, Grep, Glob, commit_cycle)
- **3 ask tools** (Bash, Read, Grep — read-only subset)
- **SQLite + FTS5** for storage and retrieval (FTS5 sync via triggers)
- **macOS-first daemon workflow** today

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

Syke uses Anthropic's Claude Agent SDK internally and supports multiple LLM backends. The interface is always Claude Messages API — providers that speak a different protocol get translated.

```
                         ┌──────────────────────┐
                         │  Claude Agent SDK     │
                         │  (always Messages API)│
                         └──────┬───┬───┬───────┘
                                │   │   │
              ┌─────────────────┘   │   └─────────────────┐
              ▼                     ▼                     ▼
┌─────────────────────┐ ┌───────────────────┐ ┌──────────────────┐
│   Anthropic-native  │ │   LiteLLM Proxy   │ │   Codex Proxy    │
│─────────────────────│ │───────────────────│ │──────────────────│
│ claude-login        │ │ 127.0.0.1:{PORT}  │ │ Claude ↔ OpenAI  │
│ openrouter          │ │                   │ │ Responses API    │
│ zai                 │ │ ► azure           │ │                  │
│ kimi                │ │ ► openai          │ │ ► ChatGPT Plus   │
│                     │ │ ► ollama          │ │   via codex CLI  │
│ (direct, no proxy)  │ │ ► vllm            │ │                  │
│                     │ │ ► llama-cpp       │ │                  │
└─────────────────────┘ └───────────────────┘ └──────────────────┘
```

**Provider resolution** (`syke/llm/providers.py`): CLI `--provider` flag → `SYKE_PROVIDER` env var → `auth.json` active_provider → auto-detect.

### LiteLLM Gateway

LiteLLM runs as a local HTTP proxy (127.0.0.1, random port) that accepts Claude Messages API requests and translates them to the upstream provider's format. Wildcard model config routes any model name to the configured upstream. Singleton pattern — one proxy per process.

**Reasoning model streaming patch** (`syke/llm/litellm_proxy.py`): LiteLLM has a bug where `reasoning_content` chunks get block type `"text"` instead of `"thinking"`, crashing Claude Agent SDK. Syke monkey-patches this at proxy startup. Version-gated to LiteLLM <1.90.0 — self-removes when upstream ships the fix.

### Environment Isolation

`clean_claude_env()` strips auth vars from subprocess environment to prevent credential leakage between providers. Each provider gets a clean env via `build_agent_env()`.

### Auth & Config

Credentials stored in `~/.syke/auth.json` (managed by `syke auth set`). Non-secret provider settings in `~/.syke/config.toml` under `[providers.<name>]`. See `docs/CONFIG_REFERENCE.md` for the full setting catalog.
