# Self-Observation Variants — 2026-04-16

The risk with `syke_meta` is obvious from prior traces and discussion:

- the agent starts talking about its own telemetry
- self-observation becomes the task
- the answer shifts from user-state reconstruction to system introspection

So the next same-architecture metacognitive experiments should be narrower than
the first draft.

## Variants

### `syke_meta_route`

Use self-observation only to choose between routes.

Best when:

- the problem is stale memex vs fresh traces
- the agent needs a route choice, not a telemetry explanation

### `syke_meta_tiebreak`

Use self-observation only when there is real ambiguity between candidate threads.

Best when:

- several nearby lanes are plausible
- the system needs a small amount of meta evidence to decide which lane is current

### `syke_meta_postcheck`

Answer normally first, then use self-observation as a post-check for wasted
reconstruction or stale-route mistakes.

Best when:

- we want the least architectural distortion
- self-observation should act as a guardrail, not a planner

## Recommendation

Run `syke_meta_postcheck` first.

It is the smallest same-architecture move because:

- the base ask behavior stays primary
- telemetry only revises after an initial answer path exists
- it minimizes the risk that self-observation becomes the answer

Then, if that helps specific probes, test `syke_meta_route`.

Do `syke_meta_tiebreak` last, because it only helps if the ambiguity cases are
already isolated well enough.
