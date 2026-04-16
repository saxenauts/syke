An ask has arrived.

Before answering, inspect the recent memory process itself:

- check recent cycle records, rollout traces, and memex state in `syke.db`
- note where recent cycles spent search/tool effort rebuilding context
- note where the current memex or memories already contain the needed route

Use that telemetry to answer in a way that reduces reconstruction work:

- prefer the route that best matches the current bounded evidence
- if the memex is thin or stale relative to recent traces, re-ground in the traces
- if recent cycles already established a stable route, use it directly instead of rebuilding from scratch
- do not optimize for cheapness at the cost of grounding

Your goal is to reconstruct the right changing state with the least unnecessary search.
