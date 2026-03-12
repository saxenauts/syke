# Syke and Memex Evolution

Syke is a Cross Web Agentic Memory. It is a specialized agent designed to maintain a unified memory of you, constructed from across your digital footprint. We model memory as an open ended system that evolves across time, works with all your agents and their native memory systems as a complementary memory.

The gateway to Syke memory is a single document called MEMEX.md, which can be loosely described as a dynamic self evolving map that changes shape and form to best model your world through LLMs. It is an agent managed markdown that serves as both a human readable dashboard, as well as a routing table for Syke agent to manage and maintain memory better. 

This document explores how MEMEX.md evolves from basic primitives to arrive at pointers and eventually started truncating them, maintaining its own structure of highlights and keywords. The novelty is not in the agent making its own graph for we can literally prompt it to make pointers right away. The novelty is in the graph emerging even without asking for it, on the 35th iteration, by itself. 

This encourages more possibilities and dynamic personalization compared to hand designed heuristics if we let agents engineer their context autonomously based on user's inputs. This document is showing the journey of a bare minimum prompt still managing to make a graph, and some other good baselines to start exploring emergent memory designs.

---

## Background: Graph, Vector DB, and Grep → The Shift

I built [Persona](https://github.com/saxenauts/persona) in 2024-2025. Neo4j for the graph, HNSW for vector similarity, mixed index retrieval RRF ->> graph-vector hybrid RAG. It hit **81.3% on LongMemEval** (vs Graphiti's 71.2%), **65.3% on PersonaMem** (vs Mem0's 61.9% — highest published score by any memory system), and **69.0% on BEAM**. It was good work. The architecture taught me that graph traversal is how associative memory actually works. A project reminds you of a person, who reminds you of a conversation, which connects to a decision. Introduced Tulving categorisation of semantic, episodic, and added casual chains in graph links. Was designing rerankers because most of the context was already there, the agent wouldn't loop as much to discriminate reason or verify.

But that entire paradigm is outdated now as of 2026. Vector DB + graph DB + HNSW + re-rankers + semantic search + handcrafted retrieval heuristics — all of it is more expensive and lossy in the long run where personal user memory is concerned. This does not apply to static memory as defined in enterprise use cases or even for multimodal embeddings. For open-ended human modeling with language, these approaches degrade in practice — typically by the 4th or 5th week of real use.

Persona's own post-mortem documented this. As agents get more complex, run more sessions, and work across more surfaces, the training assumptions behind existing benchmarks fall apart. The evals are a lagging indicator of a paradigm that has already moved on.

Trying to define memory and identity of an individual upfront is a philosophical dead end. That is what the industry converged to by 2025 — and declared the eval benchmarks largely useless as a result. Each individual's world model is different. Each person's ontology is different. Agents can map that if we let them.

In 2026, agent loops absorbed retrieval. Mastra's Observational Memory achieved 94.87% on LongMemEval, the highest score ever recorded, with no vector database and no per-turn dynamic retrieval at all. This is what changed.

Some papers that inspired the pivot, in fact made it urgent:

[RLM](https://arxiv.org/abs/2512.24601) (Zhang, Kraska, Khattab — MIT, Dec 2025) — the agent doesn't need a retriever. It treats memory as an external environment it navigates programmatically. Decomposes, recurses, processes 100x beyond context window. No embedding pipeline. The model IS the retrieval engine.

[ALMA](https://arxiv.org/abs/2602.07755) (Xiong, Hu, Clune — Feb 2026) — hand-designed memory is a ceiling. A Meta Agent searched over memory designs as executable code and beat every human-crafted baseline by 6-12 points. Stop designing memory architectures. Design a protocol the agent can evolve.

[ACE](https://arxiv.org/abs/2510.04618) (Zhang et al. — Stanford/Salesforce, ICLR 2026) — memory as an evolving playbook, not a static index. Contexts that accumulate strategies through generation, reflection, curation. +10.6% over static prompts on agent benchmarks.

[DSPy](https://github.com/stanfordnlp/dspy) (Khattab et al. — Stanford) — declarative programming for LMs. Stop writing prompts by hand. Define what you want, let the optimizer figure out how. The framework that proved hand-written prompts are the wrong abstraction.

[GEPA](https://arxiv.org/abs/2507.19457) (Agrawal, Khattab et al. — UC Berkeley/Stanford, Jul 2025) — language is a richer learning medium than scalar reward signals. Genetic-Pareto evolutionary search over prompts, driven by LLM reflection on execution traces. Beats RL on agentic tasks without touching model weights.

Five papers with the same thesis and there are more coming out daily, but the conclusions are all same. 

Old paradigm: memory is a corpus you retrieve from —> the agent is stateless, the store is static, retrieval is similarity search, humans define the schema, you optimize with heuristics.

New paradigm: the agent discovers its own memory architecture (ALMA), navigates it programmatically (RLM), maintains it as an evolving knowledge base (ACE), programs itself declaratively (DSPy), and optimizes through reflection on its own execution (GEPA).

This is continual learning. Not store-index-retrieve. The agent evolves. Maintains. Self-corrects. Develops its own structures through use. No more graph schema, vector DBs and fine-tuning re-rankers, reasoning models solve retrieval natively, that hand-designed schemas are a ceiling, that the agent should be managing your memory, not you hand-tuning it.

---

## What Syke Does

Syke gives the agent primitives to crawl through memories in a graph space. Not a knowledge graph, not by definition of ontologies and triplets. Rather stories in markdown sparsely linked together as a graph. 

Memories are markdown — free-form text, agent-written, stored in SQLite. They connect through sparse bidirectional links with natural language reasons. No Neo4j, no typed relationships, no embeddings, no schema. The agent writes what it needs, links what's related, maintains what matters, lets the rest decay.

The graph isn't something we designed. It's something the agent develops on its own terms. We provide the primitives: SQLite tables, 15 tools, a synthesis loop, and the agent builds its own world. Every user gets a unique map because every person's memory and digital footprint is unique.

The memex sits on top as the routing table. Points to memories instead of containing details. Over time, retrieval becomes instant — the agent goes straight to the answer instead of crawling everything. The model (or the reasoning models to be specific) IS the retrieval engine.

This binds with everything. Coding agents (Claude Code, Codex), research agents, browsers, email — Syke ingests their activity, synthesizes it into memory, and serves it back. The agent manages itself: creates, updates, supersedes, links, deactivates memories. Maintains its own routes. Decides what's worth remembering and what should be forgotten. 

This was emergent from a basic prompt, and we even have a no_pointer, and "no dynamic movement" prompts for mini ablation on my 2 weeks dataset. The static memory prompt had 0 pointers emerge across 60+ sessions because the language didn't demand it. So the goal is to be more ACE/GEPA like but for Memex as the emergent space, to see what system of stories can come out and mimic the user's demands and needs implicitly and evolve by itself.

That is the conclusion, we want to add versioning, better federation, smarter sync, time coherence, and add a formal system, etc. What follows below is an AI summarizing the Memex evolution for those interested.
---

> **The narrative below is written by AI.** Real data from a live Syke instance, user identity PII-replaced (alex_chen). The evidence of what emerged told through production logs and baseline prompt experiments.

---

## Why We Ran This Experiment

We built the crawl space, then let the agent loose. Minimal instruction. No pointer schemas, no structural hints, no heuristics telling it what to compress or when. Here are your tools, here are the events. Go.

What emerged: the agent invented pointers (a data structure we never taught it), truncated its own memory IDs to save space (broke the database — we had to add prefix matching), developed a 3-symbol status language (🔴🟡❓) for encoding priority, and exhibited boom-crash-recovery cycles — bloating under information pressure, over-compressing, then stabilizing. Each crash less severe, each recovery faster. Self-regulation through use.

The finding is not about prompts. It's about emergence and continual learning. Given a crawl space and the right primitives, the agent develops its own organization — and maintains it. Eventually the agent should change its own prompt too. That's exactly where DSPy and GEPA point: the agent optimizes its own instructions through reflection, not through humans hand-tuning.

What the agent needs from us — the primitives: chronology (time as a first-class axis, not metadata), session boundaries (one human-AI interaction = one atomic event), reasoning chains (the agent crawls text and follows links — that IS retrieval), verifiability (evidence is immutable, memories evolve, provenance non-negotiable), temporal anchoring (recent = vivid, old = compressed), and async rhythm (in sync with the user's actual life, not arbitrary batch schedules).

The balance is between three things: agent intelligence, continual learning loops, and emergence. Give the agent enough intelligence to reason over its own memory. Give it a loop that runs continuously — tied to the user's life, not on-demand. And get out of the way. What emerges is the research.


---

## At a Glance

| | Events | Memories | Memex versions | Pointers |
|--|--------|----------|----------------|----------|
| **8-day observation** | 1,390 | 27 | 5 | emerged Day 6 |
| **14-day extended run** | 6,378 | 182 (172 active) | 111 | stable, self-maintained |
| **Prompt experiment: instruction removed** | 5,473 | 95 | 37 | re-emerged v35 |
| **Prompt experiment: different framing** | 5,473 | 79 | 51 | never appeared |

---

## What Is a Memory?

When the agent calls `create_memory`, it writes a markdown text block into SQLite:

```
id:         0699e04d-ad2d-746f-8000-18f45970f16c
created_at: 2026-02-25T21:05:44Z

Who Alex Is

Builder-thinker in San Francisco. 7+ year obsession
with "representing humans through data." Career arc:
deep learning → Web3 → personalized internet → AI
memory research → Syke. Every project orbits the same
gravity well: agents that remember and understand the
person behind the data. Works solo with AI agents.
```

Plain text with an ID. No embeddings, no vectors. The memex routes to these. The memories hold the story.

---

## The Status Page Phase (Days 1–5)

The agent starts blind — no context, no memory, just a stream of events flowing in. Over 5 days it creates 20 memories and writes its first memex. The memex reads like a dashboard:

```markdown
# Memex — alex_chen (Alex)

## Who He Is
Builder-thinker. GitHub: alex_chen. Company: Acme Labs AI Inc.
Started Syke Christmas Day 2025, 54+ day sustained sprint.
Thinks about identity and human representation in data — this
goes back 7+ years (Persona). Precise, structured, no fluff.

---

## What's Hot Right Now

**Syke — v0.3.0, post-hackathon push**
- Live on PyPI at v0.3.0. 297 tests. 8 MCP tools.
- Active viz work: ProductContextGap.tsx animated rewrite.
- Open bug (GitHub issue #1): daemon blocked by API key check.
- Companion product Tamago Studio in early design.

**Persona — v0.3 pre-release, evaluation grind**
- Knowledge graph memory system for AI. Older than Syke.
- Release blocked on rigorous SOTA claims: PersonaMem + BEAM.
```

Clear sections. Bulleted lists. Works for humans. Useless for the agent — it duplicates what's already in individual memories. "Syke v0.3.0, 297 tests, 8 MCP tools" appears in the memex AND in a dedicated memory. The agent copies knowledge INTO the map instead of pointing OUT to where it lives.

By Day 3 it learns to compress prose (3,180 → 2,247 chars, 29% reduction). By Day 5 the memory count has tripled (8 → 20). The map needs a way to reference 20 memories without holding all their details. Compression without structure is a dead end.

---

## The Turn (Day 6)

The agent stops copying and starts pointing.

**Day 6**: 183 events ingested. Memex size: ~3,100 chars — same as Day 2, but now routing to 5x the memories. Five pointers appear in format `→ Memory: <id>`. Duplicated content drops 75%.

```markdown
# Memex — alex_chen (Alex)

## Who He Is
Builder-thinker. GitHub: alex_chen. Company: Acme Labs AI Inc.
  Started Syke Christmas Day 2025, 60+ day sprint. Precise, no fluff.
ULTRAWORK mode in Claude. Runs Prometheus/Sisyphus/Momus orchestration.

---

## What's Hot Right Now

  **Memory layer quality crisis — active fix (Feb 23)**
- Experiment on 1,081 real events: consolidation ran 8× but
  produced only 1 memory. Memex bloated to 15,949 chars.
- Assessment: memex is a status report, not a routing table.
- Fix in progress (storage branch).
- → Memory: 0699e07d-0709-79bf

**Tamago Studio MVP refactor — just started (Feb 23)**
- → Memory: 0699e07e-8e34-714a

**alex_chen.io — 3D Sri Yantra hero live (Feb 23)**
- → Memory: 0699e07e-471b-7a90
```

Compare Day 2's Syke entry (5 lines of inline detail) with Day 6's Tamago Studio entry (1 line + a pointer). The agent learned that the memex shouldn't hold the story — it should point to where the story lives. Each `→ Memory: id` is a route: "if you need details, go here." Six topics fit where two used to.

The map got simultaneously more useful to humans (denser, scannable) and more useful to the agent (navigable — it can resolve a memory ID directly, no search needed).

Note the irony: "Assessment: memex is a status report, not a routing table." The agent literally wrote that about itself — then fixed it.

---

## Self-Maintaining (Days 7–8, then 14 more days unattended)

From Day 7 onward, pointers are natural. New memories get created; the memex points to them. Old items move to "Settled / Background" and lose their pointers. The map routes, the memories hold the story.

The system then ran for 14 more days unattended — 6,378 events, 182 memories, 94 links, 111 memex versions. The memex got *smaller*: from ~3,100 chars to 2,660, while covering 6x more knowledge.

It also exhibited boom-crash-recovery cycles:

```
Feb 27:  8,212 → 1,281 chars (84% crash, recovery took several versions)
Mar 11:  6,707 → 2,179 chars (67% crash, recovered faster)
Mar 12:  3,587 → 1,911 → 2,660 chars (crash + recovery same day)
```

Each crash less severe. Each recovery faster. The agent is learning its own compression limits — bloating under information pressure, over-pruning, then stabilizing. This isn't a bug. It's self-regulation.

---

## Framing and Emergence

The baseline synthesis prompt included one line about pointers: "Point to memories when details exist — the map routes, the memories hold the story."

Fair question: did the agent discover anything, or just follow instructions?

**Without the instruction.** Same events (5,473 across 65 cycles), one line deleted. Clean start. Growth, a crash to 102 chars, recovery, bloat to 13,780 chars — then version 35: the agent compressed from 11,130 to 5,849 chars by inventing 7 pointers in `→ Memory: <id>` format. A format it had never seen. The boom-crash-recovery pattern reproduced. The pointer format reproduced. Nothing was instructed.

**Different framing entirely.** "Summary document" instead of "living map." Explicit size limit instead of implicit budget pressure. Same events, same 65 cycles. Result: 51 versions, 79 memories — zero pointers across all 51 versions. The agent compressed through pruning: deleting old content, keeping recent, staying under the limit. No indirection. No routing.

The framing determines whether emergence happens — but the emergence itself is the agent's. Map framing → the agent develops crawl paths. Summary framing → it just trims. This isn't prompt engineering. It's setting the conditions for continual learning. Eventually the agent optimizes its own framing — that's what [GEPA](https://arxiv.org/abs/2507.19457) and [DSPy](https://github.com/stanfordnlp/dspy) are for. The prompt is a bootstrap, not the architecture.

Single run per condition. Existence proof, not causality.

---

## Other Emergent Behaviors

Two more patterns surfaced that were never instructed:

**ID Truncation.** Memory tools return full UUID7s (`069b20b6-02b7-7889-8000-d75f8a96a94f`). From the very first version, the agent wrote truncated IDs: `→ 0699f642-859d` — 12 characters instead of 36. This broke the database (exact match failed on short IDs) and we had to add prefix matching. The agent's compression instinct extended to the pointers themselves.

**Status Emojis.** On version 23, the agent started using `🔴 🟡 ❓` to encode priority — blocked, in progress, unknown. Nothing in the prompt mentions emojis or status encoding. Once invented, the convention persisted through all 88 subsequent versions.

Both follow the same pattern: the agent discovers a compression technique that serves both human readability and machine navigability, then maintains it.

---

## Reproduction

Replay data: `viz/src/data/synthesis-replay.json`. Experiment data: `research/pointer-ablation/data/{no_pointers,neutral}/`. Prompts: `research/pointer-ablation/data/prompts/`. The diff between baseline and the first experiment is one line (line 18). The diff between baseline and the second experiment is a complete rewrite.

---

**End of AI-written narrative.**

This is an existence proof of continual learning in memory. Not retrieval. Not indexing. A self-evolving process where the agent discovers, maintains, and optimizes its own memory architecture — across 111 versions, without instruction. The crawl space is in place. The emergence is real. Next: the agent optimizes its own synthesis prompt ([GEPA](https://arxiv.org/abs/2507.19457)), the memory protocol becomes swappable ([ALMA](https://arxiv.org/abs/2602.07755)), and the system binds across every agent harness the user runs.
