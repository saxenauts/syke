# Memex Evolution — From Status Page to Routing Table

> A real replay of Syke's synthesis agent processing 1,390 events across 8 days. The memex starts as a blank page and graduates into a self-maintained routing table — a map where the agent keeps landmarks for humans and navigation routes for itself.

The memex serves two consumers. Humans read it as a narrative: who is this person, what are they working on. The agent reads it as a routing table: where are the detailed memories, what's linked, where should I look first. This doc shows how the second purpose — routing — emerges from the first.

Data source: `viz/src/data/synthesis-replay.json`, generated from a live experiment on real user data with PII replaced.

---

## Summary

| | |
|---|---|
| **Events processed** | 1,390 (coding-assistant, github, ai-chat, email) |
| **Memories created** | 27 |
| **Links created** | 8 |
| **Memex versions** | 5 |
| **Total cost** | $2.62 |
| **Duration** | 8 days of real activity |

The design principle at work: "The memex IS the 'retrieval becomes instant' mechanism: start slow (crawl everything), get smart (go straight to the answer). Over time it becomes a routing table."

---

## Day 1 — Cold Start

**98 events ingested** (71 coding-assistant, 27 github) · $0.38 · 114s

The agent starts from nothing — no memex, no memories, no routes. It reads raw events and creates foundational memories.

**Operations:**
```
create_memory  →  "Who Alex Is"
create_memory  →  "Beacon — What It Is"
create_memory  →  "Beacon Technical Architecture"
create_memory  →  "Beacon Branding Decisions (settled Jan 12, 2026)"
```

**Result:** 0 → 4 memories. No memex yet. The agent is crawling everything — it has no map to consult, no routes to follow. Pure exploration.

---

## Day 2 — First Map (Story Mode)

**214 events ingested** (168 coding-assistant, 42 github, 4 dev-tool) · $0.45 · 189s

More events arrive. The agent creates new memories, updates existing ones, and writes the first memex.

**Operations:**
```
create_memory   →  "AWS Infrastructure & Cost Setup"
create_memory   →  "Prism — The Other Big Project"
create_memory   →  "Forge Studio — Companion Product to Beacon"
update_memory   →  "Beacon — What It Is"              ← existing memory refined
update_memory   →  "Beacon Technical Architecture"     ← existing memory refined
synthesize      →  memex created (3,180 chars)
```

**Result:** 4 → 8 memories. First memex written.

### The Map (Day 2)

```markdown
# Memex — alex_chen (Alex)

## Who He Is
Builder-thinker. GitHub: alex_chen. Company: Acme Labs AI Inc.
Started Beacon early December 2025, 45+ day sustained sprint.
Thinks about identity and human representation in data — this
goes back 5+ years (Prism). Precise, structured, no fluff.
Plans before code.

---

## What's Hot Right Now

**Beacon — v0.2.0, post-hackathon push**
- Live on PyPI at v0.2.0. 284 tests. 9 tools.
- Active viz work: FeatureTimeline.tsx animated rewrite.
- Open bug (GitHub issue #4): daemon blocked by API key check.
- Companion product Forge Studio in early design.

**Prism — v0.3 pre-release, evaluation grind**
- Knowledge graph memory system for AI. Older than Beacon.
- Release blocked on rigorous SOTA claims: PrismMem + LENS.

---

## Infrastructure State
AWS: $7,200 credits, expires Sep 2026.

## Settled / Background
Beacon architecture: Python 3.12+, Agent SDK, MCP, Next.js viz.
Prism vs Beacon: Different products, related problem space.
```

This is a status page. The memex reads like a dashboard — identity anchored, projects ranked by activity, infrastructure noted. It works for humans reading via `get_live_context`. But there are no routes. No memory IDs. No navigation pointers. If the agent needs details on "Prism eval pipeline," it has to search from scratch. The map describes the territory but doesn't show how to get anywhere.

---

## Day 3 — Compression Without Routes

**198 events ingested** (182 coding-assistant, 16 github) · $0.32 · 105s

The agent starts replacing outdated memories — three supersessions in one cycle, the first sign of self-correction.

**Operations:**
```
supersede_memory  →  "Prism — v0.3.0 Shipped"                     ← was "pre-release", now shipped
supersede_memory  →  "Forge Studio — Moving Toward MVP"            ← scope clarified
create_memory     →  "Beacon Monorepo Structure + UI Cleanup"
create_link       →  Monorepo ↔ Forge Studio ("cleanup session added the Studio access gate")
supersede_memory  →  Memex rewritten                               ← map updated to reflect new state
```

**Result:** 8 → 9 memories. Memex shrinks from 3,180 → 2,247 chars (29% compression).

### The Map (Day 3)

```markdown
# Memex — alex_chen (Alex)

## Who He Is
Builder-thinker. GitHub: alex_chen. Company: Acme Labs AI Inc.
Started Beacon early December 2025, 50+ day sustained sprint.
Uses "DEEPWORK mode" in coding sessions. Runs Atlas/Hermes/Argos
multi-agent orchestration for heavy engineering.

---

## What's Hot Right Now

**Prism — accuracy push (Jan 14-15)**
- Branch: eval/precision-fix-75plus. Target: 68.4% → 75%+.
- Hard numbers: 80Q baseline 61%, 412Q baseline 68.4%.
- PreferenceCards v2: preference_direction, valence, confidence.
- PRs #6–9 merged at v0.3.0.

**Beacon Monorepo + Forge Studio — MVP push**
- Forge Studio UI at packages/beacon-ui/.
- Jan 13–14 cleanup. Added Studio access gate.

**Beacon — post-v0.2.0, active development**
- 312 tests. 9 tools. Source attribution bug noted.

---

## Strategic Research (Jan 15)
Beacon's thesis confirmed unique — no other system does cross-
platform identity synthesis via background daemon → MCP.
MCP security concerns noted. Skills displacing tool calling.

## Infrastructure
AWS: $7,200 credits. Instances: $18/mo.
```

The map got denser. Prism jumped to the top — the agent detected higher activity from event volume and promoted it. A "Strategic Research" section appeared from AI chat conversation threads. The identity section compressed from 4 lines to 3 while gaining new details (DEEPWORK mode, multi-agent orchestration).

But it's still a status page. Every project entry carries its own details inline — branch names, test counts, version numbers. The agent is copying knowledge INTO the memex instead of pointing OUT to where that knowledge lives. Compression helps, but without routes, the memex will bloat again as more topics accumulate.

---

## Days 4–5 — The Pressure Builds

**Day 4:** 118 events · $0.33 · 147s
```
6 × create_memory  →  New topics: auth setup, strategic research, MCP security,
                       Prism eval pipeline, Beacon v0.2.3 "Retry Resilience",
                       Weekend plan
4 × create_link    →  Cross-project connections discovered
1 × supersede      →  Memex rewritten
```
Result: 9 → 19 memories. Most productive day — the agent discovered many new topics and started cross-linking them. Links bridge platforms: a GitHub commit stream connects to an AI chat research thread connects to a coding-assistant implementation session.

**Day 5:** 209 events · $0.26 · 108s
```
1 × create_memory  →  "Beacon v0.2.5 'Three Endpoints' — Shipped"
1 × supersede      →  Memex updated
```
Result: 19 → 20 memories. The agent recognized most events as updates to existing knowledge, not new topics. But the memory count has tripled since Day 2 — the map needs a way to reference 20 memories without holding all their details.

---

## Day 6 — The Map Becomes a Routing Table

**183 events ingested** (108 coding-assistant, 62 github, 13 ai-chat) · $0.27 · 97s

This is the turn. The agent stops copying details into the memex and starts pointing to them.

**Operations:**
```
create_memory  →  "Personal Site — Penrose Tiling / Sacred Geometry Hero Section"
update_memory  →  Memex refined
```

### The Map (Day 6)

```markdown
# Memex — alex_chen (Alex)

## Who He Is
Builder-thinker. GitHub: alex_chen. Company: Acme Labs AI Inc.
Started Beacon early December 2025, 50+ day sprint. Precise, no fluff.
DEEPWORK mode in coding sessions. Runs Atlas/Hermes/Argos.

---

## What's Hot Right Now

**Memory layer quality crisis — active fix (Jan 18)**
- Experiment on 943 real events: consolidation ran 8× but
  produced only 1 memory. Memex bloated to 12,847 chars.
- Assessment: memex is a status report, not a routing table.
- Fix in progress (rewrite branch).
- → Memory: b7a3f21c-4d09-7e2f

**Storage layer philosophy shift (Jan 18)**
- Three new principles locked: "ledger not disk", identity
  for human+agent ensembles, markdown→DB trajectory.
- → Memory: b7a3f21c-bc17-6f94

**Forge Studio MVP refactor — just started (Jan 18)**
- → Memory: b7a3f21d-9f21-7c3b

**Issue #7 (ask() timeout) — closed (Jan 18)**
- → Memory: b7a3f21d-15e8-6a47

**Beacon v0.2.5 "Three Endpoints" — shipped (Jan 17)**

**Personal site — Penrose tiling hero live (Jan 18)**
- → Memory: b7a3f21d-58c3-7d19

---

## Infrastructure
AWS: $7,200 credits, expires Sep 2026. Instances: $18/mo.

## Settled / Background
Beacon: Python 3.12+, Agent SDK, MCP. 341 tests.
Prism: Eval pipeline audit ongoing. Accuracy baseline
(68.4%) may be unreliable.
```

The `→ Memory: id` pointers are the routing mechanism. Compare Day 2's Beacon entry (5 lines of inline detail) with Day 6's Forge Studio entry (1 line of context + a pointer). The agent learned that the memex shouldn't hold the story — it should point to where the story lives. Each pointer is a route: "if you need details, go here."

This changes what the memex IS. On Day 2, it was a status page humans could read. On Day 6, it's both: humans still get the 1-2 line summary of each topic, and the agent gets a memory ID it can resolve directly — no search needed, no crawling, just follow the pointer. The "retrieval becomes instant" mechanism from the design is now working.

The identity section compressed to 2 lines. The "What's Hot" entries went from multi-paragraph descriptions to compact summaries with routes. Six topics fit where two used to. The map got simultaneously more useful to humans (denser, scannable) and more useful to the agent (navigable, routable).

Note the irony: "Assessment: memex is a status report, not a routing table." The agent literally wrote that about itself — then fixed it by adding the pointers.

---

## Days 7–8 — Steady State

**Day 7:** 152 events · $0.31
```
6 × create_memory  →  New topics: memory replay experiment, consolidation fixes,
                       storage philosophy, timeout fix, personal site, studio refactor
2 × create_link    →  "Experiment produced the assessment; fixes are the direct response"
1 × update_memory  →  Memex updated
```

**Day 8:** 218 events · $0.30
```
4 × create_memory  →  New: email integration, rewrite branch merge,
                       ingestion gap, design doc update
1 × supersede      →  Memex superseded with latest state
```

The system is in steady state. New events produce new memories when genuinely novel. Existing knowledge evolves via updates and supersessions. The memex stays compact (~3,100 chars) despite the memory count tripling from Day 2. Routes are now natural — the agent doesn't deliberate about whether to include a pointer; it just does.

---

## The Arc

The memex graduated through three phases, each visible in the snapshots above:

**Phase 1 — Story mode (Days 1-3).** The agent writes everything it knows into the memex. Detailed project descriptions, version numbers, branch names, test counts — all inline. The memex works for humans but offers the agent nothing it couldn't get from search. Compression helps (Day 3 shrinks 29%) but doesn't change the fundamental problem: the map holds the territory instead of mapping it.

**Phase 2 — Pressure (Days 4-5).** Memory count triples. Cross-platform links form. The agent has too much knowledge to fit inline. The memex either bloats or finds a new strategy.

**Phase 3 — Routing (Day 6+).** The agent starts pointing instead of copying. `→ Memory: id` pointers appear. Each entry becomes a landmark with a trail — enough context for a human to understand what's happening, plus a direct route for the agent to follow. The map becomes a routing table.

This isn't accidental. The synthesis prompt says "the memex is a map, not a report" and "point to memories when details exist — the map routes, the memories hold the story." The design created conditions for routing to emerge: free-form text (no template constraints), memory IDs as first-class references, and a prompt that rewards density over verbosity. The agent discovered pointers because the architecture made them the natural solution to "too much knowledge, too little space."

---

## Two Consumers, One Map

The memex is read in two very different ways:

**`syke context` → Human consumer.** When any AI tool reads the memex (or calls `syke context`), it gets the memex as-is. A human (or an LLM acting on behalf of a human) reads it as narrative: who is this person, what are they working on, what's the context. The 1-2 line summaries per topic are the useful part. The `→ Memory: id` pointers are noise — a human doesn't know what `b7a3f21c-4d09-7e2f` means.

**`ask()` → Agent consumer.** When the ask agent needs to answer a question ("What's the status of the storage rewrite?"), it reads the memex first. The route pointers are the useful part — instead of running `search_memories("storage rewrite")` and hoping for relevant results, the agent sees `→ Memory: b7a3f21c-bc17-6f94` right there in the memex and resolves it directly. Zero search, instant retrieval. Over time, as the agent adds more routes, more queries hit the memex directly instead of requiring deep crawls.

This is the "retrieval becomes instant" mechanism. Early on (Days 1-3), every question requires a full search. After routing emerges (Day 6+), the memex shortcuts most queries to the right memory in one hop. The map got smarter.

---

## Costs

| Phase | Days | Avg cost/day | Total |
|-------|------|-------------|-------|
| Bootstrap | 1-2 | $0.42 | $0.83 |
| Deepening | 3-4 | $0.33 | $0.65 |
| Integration | 5-6 | $0.27 | $0.53 |
| Consolidation | 7-8 | $0.31 | $0.61 |
| **Total** | **8** | **$0.33** | **$2.62** |

Cost decreases as the agent learns the landscape. Bootstrap is expensive (building from scratch). Steady state is cheap ($0.26-0.33/day).

---

## Data Format

The raw replay data is at `viz/src/data/synthesis-replay.json`. Each day entry contains:

```
day              → date
events_ingested  → count + breakdown by source
cost_usd         → synthesis cost for this cycle
reasoning_trace  → ordered tool calls the agent made
delta            → memories added, updated, superseded, links created
operations       → detailed log of each operation with memory IDs
memex_snapshot   → full memex text (when available)
```

The underlying audit trail lives in the `memory_ops` SQLite table — every operation the synthesis agent makes is logged with timestamps, affected memory IDs, and input/output summaries. This is both an audit trail and future training data.

See [Architecture](ARCHITECTURE.md) for the full three-layer memory system design.
