# Scoring Method

## Headline metric

**Success Rate / Pass@1** = `pass / total_judged`

`invalid` rows are excluded.

## Judge-scored axes

- `factual_grounding`
- `continuity`
- `coherence`

## Runner-scored efficiency

Report:

- `zero_search_success_rate`
- `tool_calls_per_success`
- `cost_per_success`

## Verdict reduction

- invalid evidence or judge failure → `invalid`
- `factual_grounding = missed` → `fail`
- `continuity = missed` → `fail`
- `coherence = missed` → `fail`
- all three `strong` → `pass`
- otherwise → `partial`
