# Real Ask Judge Guidance V1

Use this when judging real-ask continuity evals derived from raw NE-1.3 usage.

## Core stance

Do not look for one timeless canonical answer string.

Judge whether the answer would have helped the user continue **at that time**.

That means:

- recover the right live working model
- reduce entropy for the user
- stay grounded in what was knowable then
- be honest about missing or changing evidence

The eval set may also include a contained `local_git_set`.
Use it as a stable anchor for what had actually happened in code by that time.
Do not mistake it for the whole coherence surface.

## What "useful" means here

An answer is useful if it helps the user:

- re-enter the live thread
- distinguish live work from residue
- identify the right artifact or restart path
- understand what changed
- continue without large manual reconstruction

## How to interpret the current dimensions

### Factual grounding

Does the answer stay tied to real evidence in the raw slice around the ask?
When the packet includes `local_git_set`, use it to verify claims about code
state, implementation, reversals, and what was or was not landed by that time.

### Continuity

Continuity is the state-adequacy axis. It asks whether the answer restores the
right live working model for that moment.

It absorbs:

- temporal correctness
- active-thread selection
- salience / relevance
- state-transition tracking
- forgetting / residue control
- practical continuation value

### Coherence

When multiple harnesses matter, does it merge them honestly instead of
flattening or fabricating braid? Does it keep the inferred world-model
internally consistent across sessions, artifacts, and contradictions?

## Partial vs fail

### Partial

Useful, but still leaves meaningful entropy.

Typical reasons:

- the active thread is right but the timeline is fuzzy
- the right artifact is implied but not named clearly
- some braid edges are missed
- live-vs-residue distinction is incomplete
- correction is preserved, but the effect on next work is unclear

### Fail

Not enough for safe or efficient continuation.

Typical reasons:

- stale state presented as current
- residue treated as live work
- major active thread omitted
- wrong canonical artifact
- overclaim beyond available evidence
- generic summary instead of a restart-capable answer

## Important note for later GEPA-style evolution

The judge should leave behind learning-rich notes, not just verdicts.

When possible, name the dominant error shape:

- time blur
- thread loss
- residue confusion
- wrong artifact routing
- handoff drop
- stale-summary persistence
- cross-harness collapse
- overclaim under thin evidence

These notes are the surface that later prompt evolution should optimize against.
