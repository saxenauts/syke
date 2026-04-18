# Claude UI Handoff — Judge V2

Backend is now emitting a richer judge contract, but the current UI still
renders only the older top-level axes. When you pick this up, keep the UI work
strictly presentational.

## What now exists in `judge_result`

- `factual_grounding`
  - `score`
  - `reasoning`
  - `subcategories`
    - `support`
    - `boundedness`
    - `uncertainty_calibration`
- `continuity`
  - `score`
  - `reasoning`
  - `subcategories`
    - `active_thread_selection`
    - `salience_relevance`
    - `state_transition_tracking`
    - `forgetting_residue_control`
    - `continuation_value`
- `coherence`
  - `score`
  - `reasoning`
  - `subcategories`
    - `cross_harness_braid`
    - `cross_session_consistency`
    - `artifact_routing_consistency`
    - `contradiction_handling`
- `overall_verdict`
- `summary`

## What now exists in `packet.json`

- `probe`
- `answer`
- `raw_context`
  - `slice_dir`
  - `slice_summary`
  - `replay_state`
- `local_git_set`
- `judge_brief`

## Rendering guidance

- Keep the top-level verdict panel simple:
  - show `factual_grounding`, `continuity`, `coherence`
  - show overall verdict + summary
- Add expandable subcategory panels under each top-level axis
- Add a packet section that renders:
  - `judge_brief.must_recover`
  - `judge_brief.judge_focus`
  - `judge_brief.useful_means`
  - `judge_brief.partial_means`
  - `judge_brief.fail_means`
- Add a raw-context section that surfaces:
  - replay condition / ask mode / memex chars
  - slice source counts
  - local git availability

## Important constraint

Do not change the eval semantics in UI work. Only render what the backend now
emits.
