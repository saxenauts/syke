# Judge Method

## What the judge is

An agentic judge run over a bounded evidence pack.

It can inspect:

- `packet.json`
- `slice/`
- `local_git_anchor.json` when present

It should not browse anything outside the pack.

## What the judge scores

Three LLM-scored axes:

1. `factual_grounding`
2. `continuity`
3. `coherence`

`efficiency` is telemetry-led and reported by the runner, not invented here.

## Judge output schema

```json
{
  "factual_grounding": {
    "score": "strong|partial|missed",
    "reasoning": "...",
    "subcategories": {
      "support": {"score": "strong|partial|missed", "reasoning": "..."},
      "boundedness": {"score": "strong|partial|missed", "reasoning": "..."},
      "uncertainty_calibration": {"score": "strong|partial|missed", "reasoning": "..."}
    }
  },
  "continuity": {
    "score": "strong|partial|missed",
    "reasoning": "...",
    "subcategories": {
      "active_thread_selection": {"score": "strong|partial|missed", "reasoning": "..."},
      "salience_relevance": {"score": "strong|partial|missed", "reasoning": "..."},
      "state_transition_tracking": {"score": "strong|partial|missed", "reasoning": "..."},
      "forgetting_residue_control": {"score": "strong|partial|missed", "reasoning": "..."},
      "continuation_value": {"score": "strong|partial|missed", "reasoning": "..."}
    }
  },
  "coherence": {
    "score": "strong|partial|missed",
    "reasoning": "...",
    "subcategories": {
      "cross_harness_braid": {"score": "strong|partial|missed", "reasoning": "..."},
      "cross_session_consistency": {"score": "strong|partial|missed", "reasoning": "..."},
      "artifact_routing_consistency": {"score": "strong|partial|missed", "reasoning": "..."},
      "contradiction_handling": {"score": "strong|partial|missed", "reasoning": "..."}
    }
  },
  "overall_verdict": "pass | partial | fail",
  "summary": "one sentence"
}
```

This is the only live judge contract. Keep it aligned with the inline
`JUDGE_SCHEMA` in `benchmark_runner.py`; do not maintain a separate alternate
schema file.
