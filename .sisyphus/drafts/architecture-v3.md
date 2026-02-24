# Syke Memory Architecture v3 — Final Foundation

> Saved Feb 24 2026. Incorporates Oracle stabilization review + user clarifications.
> Previous: v2 (RLM-informed), v1 (pre-research). Both preserved.
> Oracle sessions: ses_37246f8e4ffebzCWIoQ0z39yOq (stabilization), ses_372643490ffepOQCq4l6u0VnKo (RLM review)

---

## What This Version Finalizes

v3 is the **shipping foundation**. It implements what v2 proposed and closes the open questions Oracle was asked.

| Decision | Answer |
|---|---|
| Story vs Memex | Separate. Story = prose narrative (a memory). Memex = map/routing table (a special memory). One-way: Memex indexes Story, never reverse. |
| Maintenance burden | Bounded by design. Links = lossy cache with decay. Active working set capped. Decay only during synthesis, zero cost when idle. |
| Temporal coherence | Sessions are atomic (P3). Temporal views computed on demand. WeekDigest = future materialization when useful. |
| Inspiration mapping | Precise: RLM (tools not context stuffing), ALMA (stable API, swappable strategy), LCM (hierarchical compaction), Syke-native (11 principles). |
| Library ecosystem | Our own primitives. DSPy RLM = reference for interaction pattern. Mastra = borrow observation concept. |
| The Map | P11 added. Memex IS the map. Fog of war clearing. Landmarks + trails + world state. |

---

## The 11 Principles

P1-P10 unchanged (BRAINSTORM.md).

**P11 — The Map Appears**: With each use, the agent builds its own world. Like fog of war clearing — landmarks emerge, trails form, the map gets richer. The memex IS the map. Not designed top-down; it appears bottom-up from exploration. Every user gets a unique map.

---

## Taxonomy (Final)

| Term | Meaning |
|---|---|
| **Memory** | Any unit of knowledge — person, project, preference, story, todo. Free-form text. |
| **Link** | Sparse connection. Natural language reason. Lossy cache, not permanent graph. |
| **Evidence** | Raw events in the ledger — immutable, timestamped, sourced. |
| **Memex** | The map. Agent's world model + routing table. Landmarks + trails + world state. |
| **Story** | Prose narrative. Separate from memex. Human-readable account. A type of memory. |
| **Synthesis** | Agent processes events → creates/updates/retires memories → evolves the map. |
| **Decay** | Retirement, not deletion. Stays in ledger, leaves active search. Only during synthesis. |
| **Solidification** | Knowledge hardens through repeated reinforcement. Not a mechanism — natural result of the agent keeping a memory alive across sessions. |

---

## Architecture: What Exists Now

### Layer 1: Evidence Ledger (unchanged)
SQLite + WAL + FTS5. Append-only events. 4700+ events. Immutable.

### Layer 2: Memories (15 tools now)
Free-form text, agent-written. Full CRUD exposed:

**Write tools** (synthesis agent + ask agent):
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
get_memex()                                 → the map
get_recent_memories(limit)                  → newest first
browse_timeline(since, before, source)      → time-windowed events
cross_reference(topic)                      → search across all platforms
```

### Layer 3: Memex (the map)
Special memory (`source_event_ids = ["__memex__"]`). Structure:

```markdown
# Memex — {user}

## Landmarks (stable entities)
[mem_xxx] Project Name — one-line status
[mem_yyy] Person — relationship context

## Active Trails
Topic → search 'keyword' or follow_links(mem_xxx)
Recent → browse_timeline(since=last_week)

## World
Sources: claude-code, github, chatgpt. N events. Last sync: date.
```

### Layer 4: Memory Ops (audit + future training data)
Every operation logged: create, update, supersede, deactivate, link, synthesize.
Schema ready for future RLM/RL training data.

---

## Synthesis Loop (Updated)

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
  Rewrite memex with current landmarks, trails, world state.
  Memex stays compact — it's a navigational index, not a report.
```

---

## Memory Lifecycle

```
soft    → record() or synthesis creates it
active  → agent sees it reinforced across sessions
solid   → repeatedly confirmed, becomes a memex landmark
dormant → user goes quiet → NOTHING HAPPENS
          memory sits in SQLite, still queryable, zero maintenance
          when user returns, everything is where they left it
```

Decay only runs during synthesis. No synthesis = no decay. Permanent by default.

---

## Precise Inspiration Mapping

### RLM (arxiv 2512.24601)
**Borrowed**: Agent interacts with memory via tools/programs, not context stuffing. ask()/sub_ask() as constrained RLM.
**Not yet**: Full code execution over memory (Tier 2.2 sub_ask).

### ALMA (arxiv 2602.07755)
**Borrowed**: Stable API (create/update/retrieve/link/deactivate/log), strategy as swappable code.
**Not yet**: Offline meta-learning, runtime strategy codegen.

### LCM (Voltropy, Feb 2026)
**Borrowed**: Hierarchical thinking (session → week → month → theme).
**Not yet**: Deterministic compaction, engine-managed recursion.

### Syke-native
Session atomicity, ledger-not-disk, evidence ≠ inference, sparse links, agent crawls text, portable SQLite, the map appears (P11), solidification through use, dual access fast+deep, accept change.

---

## What's Implemented (This Session)

| File | Change | LOC |
|---|---|---|
| `syke/db.py` | Added `get_memory_chain()` | +38 |
| `syke/memory/tools.py` | Added 6 tools, updated MEMORY_TOOL_NAMES (9→15) | +185 |
| `syke/memory/synthesis.py` | Rewrote synthesis prompt (orient → evolve → update map) | ~55 rewritten |
| `syke/distribution/ask_agent.py` | Added 3 read tools to ASK_TOOLS | +3 |
| `BRAINSTORM.md` | P11 added, terminology table updated | +8 |

358 tests passing (340 existing + 18 new tool tests). Zero regressions.

---

## What's Next (After Foundation)

1. **Run 7-day simulation** — verify synthesis creates/updates/evolves memories and map
2. **RLM/ALMA integration** — the "real thing" (environmental loop, self-evolving strategy)
3. **Claude Desktop adapter** — ingest sessions from `~/Library/Application Support/Claude/`
4. **Release to PyPI** — once simulation proves the system works
5. **Story as separate artifact** — derive prose narrative from memories
6. **WeekDigest materialization** — computed temporal views

---

## References

- RLM: arxiv 2512.24601 (Zhang, Kraska, Khattab — MIT CSAIL, Dec 2025)
- ALMA: arxiv 2602.07755 (Xiong, Hu, Clune — UBC/Vector, Feb 2026)
- LCM: papers.voltropy.com/LCM (Feb 2026)
- Oracle stabilization: ses_37246f8e4ffebzCWIoQ0z39yOq
- Oracle RLM review: ses_372643490ffepOQCq4l6u0VnKo
- OpenClaw ecosystem: strategies/dist/openclaw.md
- Letta filesystem: research/memory-mcp-landscape.md
