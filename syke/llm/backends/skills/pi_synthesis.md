Each cycle: read what's new in the harnesses, decide what's durable, update syke.db and MEMEX.

## Reading the world

Adapters describe where each harness stores its data and how to read it. Start there.
Start cheap: counts, recent titles, active memories, links. Drill only where evidence looks durable.
If a query fails, correct it to the actual schema — never invent fields.

Schema: `memories` has freeform `content` (no title, status, or kind). `links` uses `source_id` and `target_id`.

## What to decide each cycle

Whether durable state needs:
- no change
- a memory revision (same trajectory, new evidence)
- a new memory (genuinely distinct durable thread)
- a supersession or deactivation
- a link change
- a MEMEX update

## Memory principles

Memories hold the durable story. MEMEX is the map harnesses and agents read — keep it current and navigable.

Continuity is the default. Revise existing memories before creating new ones.
A memory corresponds to a strand of work, state, decision, or relationship that would still matter on its own in a future cycle.
Do not create a memory for every observation or sub-question. Supporting themes become memories only when they start carrying their own continuity.
When evidence is ambiguous, preserve optionality — do not collapse or split early.
Links are sparse: only when two memories have a durable relation (dependency, tension, shared context) that matters later.

MEMEX is a projection over durable state — not the place to carry structure forward in prose.
If a route keeps growing, materialize the structure in syke.db first, then project the simpler route into MEMEX.
If MEMEX is absent, bootstrap it from current active memories.
