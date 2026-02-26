# Syke Memory Architecture

> How Syke builds a living, self-evolving model of who you are.

---

## Design Philosophy

Syke's memory system treats **everything as language**. No embeddings, no typed relationship taxonomies, no rigid schemas. The LLM reads text, writes text, and navigates text — the same way it processes anything else.

**Core ideas:**
- **Sessions are atomic** — a Claude Code session about "refactoring auth" is one unit of intent, not 50 messages
- **Evidence ≠ inference** — raw events (what happened) are immutable; memories (what it means) are mutable and agent-written
- **The agent crawls text** — FTS5/BM25 for retrieval, LLM for understanding. No vector DB needed.
- **Sparse links over dense graphs** — a few meaningful connections with natural language reasons, not relationship taxonomies
- **Portable by default** — one SQLite file per user, copy it anywhere
- **The map appears** — the agent builds its own world model with each use, like fog of war clearing

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
├── event_type: "session" | "commit" | "conversation" | ...
├── title: string
├── content: text (full session/commit/email content)
└── metadata: JSON (source-specific fields)
```

Events are never modified. This is the ground truth — everything else is derived.

### Layer 2: Memories

Free-form text units of knowledge, written and maintained by the synthesis agent. A memory can be anything: a person, a project, a preference, a decision, a story.

```
memories table
├── id: UUID7
├── user_id: string
├── content: text (free-form markdown, agent-written)
├── source_event_ids: JSON array (evidence that created this)
├── created_at / updated_at: timestamps
├── superseded_by: UUID7 | null (points to replacement)
└── active: boolean (false = retired, still queryable)
```

**15 tools** expose full CRUD to the agent:

**Write tools** (synthesis agent):
```
create_memory(content, source_event_ids)    → new memory
create_link(source_id, target_id, reason)   → new link
update_memory(memory_id, new_content)       → edit in place (minor changes)
supersede_memory(memory_id, new_content)    → replace (major changes, keeps history)
deactivate_memory(memory_id, reason)        → retire (stays in ledger)
```

**Read tools** (ask agent + synthesis agent):
```
search_memories(query)                      → FTS5/BM25 search
search_evidence(query)                      → search raw events
follow_links(memory_id)                     → linked memories + reasons
get_memory(memory_id)                       → full content by ID
list_active_memories(limit)                 → compact index (ID + first line)
get_memory_history(memory_id)               → supersession chain
get_memex()                                 → the map (see Layer 3)
get_recent_memories(limit)                  → newest first
browse_timeline(since, before, source)      → time-windowed events
cross_reference(topic)                      → search across all platforms
```

### Layer 3: Memex (The Map)

A special memory (`source_event_ids = ["__memex__"]`) that acts as the agent's accumulated understanding of this person. It's compact, navigational, and evolves with every synthesis cycle.

```markdown
# Memex — {user}

## What's Happening Now (stable entities)
[mem_xxx] Project Name — one-line status
[mem_yyy] Person — relationship context

## Patterns & Threads
Topic → search 'keyword' or follow_links(mem_xxx)
Recent → browse_timeline(since=last_week)

## Context
Sources: claude-code, github, chatgpt. N events. Last sync: date.
```

The memex is NOT a report — it's a map. The agent reads this first, then navigates. It self-organizes based on what's actually important to this person — no prescribed structure.

### Layer 4: Memory Ops (Audit Trail)

Every operation is logged: create, update, supersede, deactivate, link, synthesize. This serves two purposes:
1. **Audit** — full history of what the agent did and why
2. **Training data** — future reinforcement learning over memory decisions

---

## The Synthesis Loop

Runs after new events are ingested (daemon syncs every 15 minutes).

```
STEP 1 — ORIENT:
  Read memex (the map). Understand what exists.
  Read new events since last synthesis.

STEP 2 — EXTRACT & EVOLVE:
  For each new event, decide:
  a) New knowledge? → create_memory + create_link
  b) Updates existing? → update_memory or supersede_memory
  c) Makes something obsolete? → deactivate_memory
  d) Not worth remembering? → Skip

STEP 3 — UPDATE THE MAP:
  Rewrite memex with current state: what's active, key entities, temporal signals.
  Memex stays compact — it's a navigational index, not a dump.
```

The agent has full agency over memory decisions. It decides what's worth remembering, how to organize it, when to retire old knowledge. No heuristics — just language.

---

## Memory Lifecycle

```
soft    → synthesis creates it from new events
active  → reinforced across multiple sessions
solid   → repeatedly confirmed, becomes a key reference in the memex
dormant → user goes quiet → NOTHING HAPPENS
          memory sits in SQLite, still queryable, zero maintenance
          when user returns, everything is where they left it
```

Memories are permanent by default. Decay only runs during synthesis — if there's no synthesis (user is inactive), nothing decays. Zero maintenance cost.

---

## How ask() Works

When a user (or another AI tool) asks a question via the CLI (`syke ask`):

1. Agent reads the **memex** first — the map orients it
2. Agent uses **read tools** to navigate: search memories, follow links, browse timeline
3. Agent synthesizes an answer from what it finds
4. Answer is grounded in evidence — the agent can cite specific events and memories

The ask agent has access to all read tools but no write tools. It explores the existing knowledge base without modifying it.

---

## Key Design Decisions

### Why SQLite over vector DB?

Semantic understanding happens in the LLM, not the database. FTS5 with BM25 ranking handles keyword retrieval. The LLM decides what's relevant from the results. SQLite gives us ACID transactions, concurrent reads (WAL mode), zero infrastructure, and a single portable file.

### Why free-form text over structured schemas?

The agent organizes knowledge the way it naturally thinks — in prose, markdown, lists, whatever fits. A memory about movie preferences might have categories like "with gf", "period films", "comfort watches" — organic structure that emerges from use, not imposed by schema.

### Why supersession over versioning?

When knowledge changes significantly, the old memory is deactivated and a new one takes its place. The chain is preserved: `get_memory_history()` walks the supersession links. This is simpler than version control and matches how human memory works — you don't version your beliefs, you update them.

### Why sparse links over dense graphs?

A movie night memory links to a person, maybe to the movie, maybe to "date nights." Three links with natural language reasons ("watched together", "part of period film collection"). Not 50 typed relationships. The agent adds links when they're genuinely useful for navigation.

### Why a separate memex?

Without a map, the agent would need to search blindly every time. The memex gives it orientation — what exists, where to look, what's currently important. It's the difference between exploring a city with and without a map.

---

## Inspiration

Syke's memory architecture draws from several research directions:

**[RLM — Recursive Language Models](https://arxiv.org/abs/2512.24601)** (Zhang, Kraska, Khattab — MIT CSAIL, Dec 2025): Treats long prompts as an external environment the LLM programmatically examines, decomposes, and recursively calls itself over. Syke borrows the core idea: memory lives outside the context window, and the agent navigates it via tools rather than stuffing everything into the prompt.

**[ALMA — Automated Meta-Learning of Memory designs for Agentic systems](https://arxiv.org/abs/2602.07755)** (Xiong, Hu, Clune — Feb 2026): A Meta Agent searches over memory designs (database schemas, retrieval and update mechanisms) expressed as executable code, outperforming hand-crafted designs by 6-12 points. Syke's takeaway: design around a pluggable `update()`/`retrieve()` protocol so the memory architecture can evolve without rewriting the agent.

**[LCM — Lossless Context Management](https://papers.voltropy.com/LCM)** (Ehrlich, Blackman — Voltropy, Feb 2026): Decomposes RLM-style recursion into deterministic, engine-managed primitives — a DAG-based hierarchical summary system that compacts older messages while retaining lossless pointers to originals. Syke's takeaway: hierarchical compression where recent context stays full, older context compacts, and nothing is truly lost.

**Syke-native**: Session atomicity, evidence ≠ inference, sparse links, agent crawls text, portable SQLite, the map appears bottom-up from exploration.

---

## File Map

```
syke/
├── db.py                      # SQLite + WAL + FTS5, all CRUD
├── models.py                  # Memory, Link, MemoryOp, Event models
├── memory/
│   ├── tools.py               # 15 memory tools (read + write)
│   ├── synthesis.py           # Synthesis agent + prompt
│   └── memex.py               # Memex read/write/bootstrap
├── distribution/
├── distribution/
│   └── ask_agent.py           # ask() agent with read-only tools
├── sync.py                    # Daemon sync cycle
└── config.py                  # Model, budget, turn limits
```

---

## Stats

- **361 tests** passing (unit + integration)
- **15 memory tools** (10 read, 5 write)
- **SQLite + FTS5** for storage and retrieval
- **~$0.25/synthesis** cycle (Sonnet, 10 turns max, $0.50 budget cap)
