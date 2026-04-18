# Environment Contract

Canonical statement of what the replay/eval harness is actually claiming.

This document is intentionally short. It does not replace implementation docs.
It pins the meaning of the environment so replay runs, benchmarks, judge prompts,
and visualizations are all interpreted the same way.

---

## Core Claim

The harness evaluates whether a memory system can reconstruct enough of the
user's evolving project state at time `t` to let the user continue usefully.

This is not a generic QA benchmark.
This is not a timeless retrieval benchmark.
This is not just "did it remember a fact?"
It is not assuming facts are stable.

The object under evaluation is:

- time-local state reconstruction
- where the right facts can change over time
- across high-variance cross-harness work
- under bounded evidence
- for ongoing agency

---

## Hidden State

The hidden state is the user's real cross-harness working world at time `t`.

That includes, in principle:

- active threads
- current priorities
- live vs stale branches
- unfinished work
- recent decisions and reversals
- facts that have changed since earlier asks
- relevant code/work-state reality
- continuation hooks the user would need next

The hidden state is not directly given to the agent.
It must be inferred from the bounded environment.

---

## Observation Surface

For one eval item at time `t`, the agent may observe only the contained
environment for that item:

- frozen harness slice up to `t`
- replay-time workspace state up to `t`
- adapter markdown describing where data lives and how to read it
- optional time-contained local git anchor for code/work-state truth
- the question itself

No future evidence.
No live-machine browsing outside the bounded slice.
No contamination from present-day Syke state.

---

## Action Surface

For the benchmark ask path, the action is:

- read the bounded environment
- query local state
- produce an answer

The conditions vary only the memory architecture visible to that ask:

- `pure` = null baseline, static psyche/world only
- `zero` = substrate-only ablation, psyche + memex, no synthesis/control block
- `syke` = full ask stack

Everything else should remain constant across conditions.

---

## Success Condition

An answer succeeds when it reconstructs enough of the live working model at
time `t` for useful continuation.

Concretely, that means the answer should help the user:

- re-enter the right live thread
- distinguish live work from residue
- identify what changed
- recover the right artifact or restart path
- continue without large manual reconstruction

This means the harness is testing whether the memory architecture can bind
variance effectively:

- changing facts
- changing priorities
- changing project shape
- different harness-local surfaces for the same user and work

---

## Judge / Reward Surface

The judge is evidence-browsing and agentic, but neutral.

It scores three axes:

- `factual_grounding`
- `continuity`
- `coherence`

`factual_grounding` answers:

- did the answer stay tied to what was actually knowable then?

`continuity` in the current implementation does **not** mean the old
"continuity vs retrieval" distinction by itself.
It is the operational name for the second step-one task axis:

- did the answer reconstruct the right changing live state for that moment?

So `continuity` intentionally absorbs:

- temporal correctness
- fact change over time
- relevance / salience
- forgetting and residue control
- practical continuation value

into one question:

- did the answer restore the right live working model for that moment?

`coherence` answers:

- when multiple harnesses or concurrent surfaces matter, did the answer keep
  the inferred world-model internally consistent across sources, sessions,
  artifacts, and contradictions?

Efficiency is also part of the environment, but it is not a free-form judge
opinion. It is deterministic telemetry reported alongside the verdict because
the system is trying to reduce real reconstruction physics:

- unnecessary tool calls
- unnecessary search
- unnecessary cost
- unnecessary operator rebuild work

Step one keeps efficiency separate from the two main judge axes so the harness
can first establish whether the architecture reconstructs the right changing
state at all, and then how efficiently it does so.

---

## Episode Semantics

At first principles, the environment distinguishes:

- `terminated`: the task naturally resolves from the bounded evidence
- `truncated`: the episode is cut off by budget, timeout, or packaging limits

This distinction matters for later learning and training use.

Today, the replay/eval stack already preserves bounded time and partial-vs-
complete run state, but future training-oriented versions should carry
`terminated` vs `truncated` explicitly as first-class fields.

---

## Contamination Rules

The environment is valid only if:

- evidence is time-contained
- conditions share the same underlying slice reality
- the judge does not inherit Syke identity or self-story
- current-run artifacts do not leak into the judged evidence surface
- live global Syke state does not leak into replay/eval workspaces

If those fail, the environment is not scientifically interpretable.

---

## What This Harness Is Not

It is not claiming:

- full personal intelligence
- general self-improvement
- solved continual learning
- closed-form verifiable rewards for all open-ended work

It is claiming one narrower thing:

- given real evolving project traces, this environment can test whether a
  memory architecture improves time-local reconstruction of the user's changing
  project world for useful continuation

That narrower claim is the foundation for later continual-learning and
training-environment work.
