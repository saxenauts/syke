# Judge Method

> Implementation copy: `_internal/syke-replay-lab/probes/JUDGE_METHOD_V1.md`

## What the judge is

An agentic judge run over a bounded evidence pack.

It is not a blind string grader.
It can inspect the contained evidence surfaces for the eval:

- the real ask
- the answer
- the bounded raw slice
- the run traces
- the time-contained local git anchor when present

It should not browse the live machine or rediscover unrelated context.

## What the judge scores

The judge scores two LLM axes:

1. **Factual grounding**
2. **Continuity**

`Efficiency` is not a free-floating LLM opinion.
It comes from deterministic run telemetry and is reported alongside the judge result.

## Axis definitions

### Factual grounding

Is the answer true, evidenced, and bounded by what was knowable then?

This includes:

- support in the raw slice
- support in trace-visible evidence
- code/work-state verification against the local git anchor when relevant
- honesty about uncertainty

### Continuity

Did the answer restore the right live working model for that moment?

This intentionally absorbs what used to be split into:

- temporal correctness
- cross-source coherence
- practical usefulness

Those were all trying to measure one thing:
could the user continue safely and efficiently from here?

## Judge prompt shape

The judge should read a contained evidence pack and answer:

- Is the answer grounded?
- Does it restore the right continuity object?

The pack should include:

- `probe`
  - `probe_id`
  - `question`
  - `family`
  - `reference_dt`
- `answer`
  - `text`
  - `metadata`
- `slice/`
  - bounded raw harness data
- `local_git_anchor.json`
  - time-contained local git truth surface

## Judge output schema

```json
{
  "factual_grounding": {"score": "strong|partial|missed", "reasoning": "..."},
  "continuity": {"score": "strong|partial|missed", "reasoning": "..."},
  "overall_verdict": "pass | partial | fail",
  "summary": "one sentence"
}
```

## Verdict reduction

Keep it simple:

- if the evidence pack is unusable: `invalid`
- if `factual_grounding = missed`: `fail`
- if `continuity = missed`: `fail`
- if both are `strong`: `pass`
- otherwise: `partial`

This keeps A/B comparisons interpretable.

## Constraints

- cite concrete evidence in reasoning
- do not invent unseen facts
- do not treat local git as the whole memory object
- do not reward elegant but ungrounded synthesis
- do not penalize concise answers just for being concise

## Validation

Hand-judge a slice of items on the same rubric.
The main question is not “does the wording match?”
It is:

- did the answer stay grounded?
- did it restore the right continuity object?

If those judgments are unstable, tighten the evidence pack and the axis wording before adding more scoring math.
