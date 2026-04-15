# Replay Lab — NE-1.3 15-Day Azure Mini

This note records the current local replay-lab state for the `NE-1.3` 15-day run on Azure `gpt-5.4-mini`.

It exists because the main replay lab lives under `_internal/`, which is repo-ignored by default. The canonical local run package is preserved on this machine at:

- `_internal/syke-replay-lab/runs/ne13_15d_azmini_merged/`

Use that directory for local analysis. This note is the tracked summary that explains what changed and where the local canonical artifact lives.

## Scope

Window:

- observed days: `2026-03-07` to `2026-03-21`
- probes: `R01` to `R19`
- environments: `pure`, `syke`, `zero`

Provider/runtime:

- provider: `azure-openai-responses`
- model: `gpt-5.4-mini`

## Replay-lab code changes

The local replay-lab runner and benchmark surfaces were updated to support this run:

- `_internal/syke-replay-lab/probes/REAL_ASK_RUNSETS.yaml`
  - added the bounded `ne13_real_15d` runset (`R01` to `R19`)

- `_internal/syke-replay-lab/benchmark_runner.py`
  - judge packet now carries exact `reference_ts_local` and `reference_cutoff_iso`
  - per-condition slices are isolated to avoid parallel materialization races
  - per-eval workspaces are unique to avoid SQLite locking across workers
  - benchmark ask traces are now persisted to local trace/evidence files
  - judge prompt explicitly forbids apology/refusal output and requires JSON even under uncertainty

## Production/runtime context

This replay-lab work sits on top of already-tracked production/runtime changes from recent commits:

- `0d61857` — isolate benchmark traces and thread model param through `pi_ask`
- `3e315c3` — narrow Pi sandbox to explicit runtime dirs
- `7090b13` — guard `stop_pi_runtime`, fix doctor wording

## Final local merged result

The best-available merged local artifact currently reports:

- `pure`: `3 pass / 7 partial / 8 fail / 1 invalid`
- `syke`: `0 pass / 7 partial / 11 fail / 1 invalid`
- `zero`: `1 pass / 7 partial / 11 fail / 0 invalid`

Remaining unrecovered invalid rollouts:

- `pure / R06`
- `syke / R18`

## Main local finding

The strongest local replay finding is that the `syke` replay prior froze:

- 15 replay cycles
- 1 memex hash
- `667` memex chars throughout
- `3` active memories throughout

That behavior is visible in the local replay rollout:

- `_internal/syke-replay-lab/runs/ne13_15d_azmini_prod/replay_results.json`

The `zero` replay showed the opposite pathology:

- memory rows kept growing
- canonical memex stayed blank

That behavior is visible in:

- `_internal/syke-replay-lab/runs/ne13_15d_azmini_zero/replay_results.json`

## Interpretation caveat

The current benchmark path is strong enough for local analysis, but it still has one important methodological caveat:

- raw benchmark slices and git cutoffs use exact ask-local timestamps
- replay memex matching is still day-granular

So same-day ask evaluation is not yet full intraday replay fidelity.
