# NE-1.3 15-Day Azure Mini Run

This directory is the committed summary package for the 15-day `NE-1.3` evaluation window on Azure `gpt-5.4-mini`.

Use standard terms for this package:

- `environment`: the evaluation setting. This run has `pure`, `syke`, and `zero`.
- `rollout`: one probe evaluated in one environment.
- `trace`: the persisted ask/judge outputs and metadata for a rollout.
- `replay rollout`: the day-by-day memory reconstruction run used to supply environment state.

## Canonical files

- `benchmark_results.json`
  The best-available merged benchmark artifact across the full run plus targeted reruns, sanitized for commit.

- `run_manifest.json`
  Machine-readable run summary using standard environment / rollout / trace language.

- `rollout_index.json`
  One entry per rollout with environment, verdict, and source run id.

- `probe_matrix.json`
  Side-by-side per-probe verdict matrix across `pure`, `syke`, and `zero`.

- `probe_matrix.csv`
  Spreadsheet-friendly export of the probe matrix.

- `config.json`
  Merge provenance: which source runs were used and which rows were replaced.

## Replay rollouts

- `syke` replay run id: `ne13_15d_azmini_prod`
- `zero` replay run id: `ne13_15d_azmini_zero`

Window:

- observed days: `2026-03-07` to `2026-03-21`
- probes: `R01` to `R19`
- total rollouts: `57`

## Final merged environment summary

- `pure`: `3 pass / 7 partial / 8 fail / 1 invalid`
- `syke`: `0 pass / 7 partial / 11 fail / 1 invalid`
- `zero`: `1 pass / 7 partial / 11 fail / 0 invalid`

Success Rate:

- `pure`: `16.7%`
- `syke`: `0.0%`
- `zero`: `5.3%`

## Remaining invalid rollouts

After the full run and targeted reruns, two rollouts remain invalid:

- `pure / R06`
- `syke / R18`

These are persistent judge refusal/dropout edge cases under Azure `gpt-5.4-mini`.

## Notes

- The merged artifact is the one to analyze next.
- This committed package is summary-only. Raw replay, ask, judge, evidence, and slice directories remain local-only and are not bundled here.
- Same-day replay state is still day-granular even though ask timestamps and git cutoffs are exact. This remains the main methodological caveat for interpretation.

## Related Landed Fixes

These tracked production/runtime fixes were already committed separately before this summary package:

- `0d61857` isolate benchmark traces and thread model selection through `pi_ask`
- `3e315c3` narrow Pi sandbox to explicit runtime directories
- `7090b13` guard runtime stop and fix `syke doctor` events/memories reporting
