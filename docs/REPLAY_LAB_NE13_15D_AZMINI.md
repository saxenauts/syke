# Replay Lab — NE-1.3 15-Day Azure Mini

This note records the current replay-lab state for the `NE-1.3` 15-day run on Azure `gpt-5.4-mini`.

It exists because the main replay lab lives under `_internal/`, which is repo-ignored by default. The original canonical merged run package was preserved in git at:

- commit `26a3ed1`
- path `_internal/syke-replay-lab/runs/ne13_15d_azmini_merged/`

This note is the tracked summary explaining what changed, what the preserved baseline numbers were, and what later runtime/judge work changed. The merged run directory may be absent from the current worktree if a parallel agent pruned or is rebuilding local run artifacts.

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
  - preserved baseline still used a prompted-JSON judge path, which later proved to be the wrong abstraction

## Production/runtime context

This replay-lab work sits on top of already-tracked production/runtime changes from recent commits:

- `0d61857` — isolate benchmark traces and thread model param through `pi_ask`
- `3e315c3` — narrow Pi sandbox to explicit runtime dirs
- `7090b13` — guard `stop_pi_runtime`, fix doctor wording

## Preserved baseline result

The preserved `26a3ed1` merged package reports:

- `pure`: `3 pass / 7 partial / 8 fail / 1 invalid`
- `syke`: `0 pass / 7 partial / 11 fail / 1 invalid`
- `zero`: `1 pass / 7 partial / 11 fail / 0 invalid`

Remaining unrecovered invalid rollouts:

- `pure / R06`
- `syke / R18`

Ask-side refusal counts in that preserved package:

- `pure`: `5`
- `syke`: `6`
- `zero`: `10`

Ask-side non-refusal benchmark fails in that preserved package:

- `pure`: `R04`, `R07`, `R11`
- `syke`: `R04`, `R08`, `R09`, `R14`, `R19`
- `zero`: `R14`

## Main preserved finding

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

## Apr 14-15 update — judge path

After the preserved baseline, the benchmark judge path was corrected to use Pi's native structured surface instead of prompted prose JSON:

- commit `9f69c36`
- `benchmark_judge` now launches a Syke-owned Pi SDK RPC wrapper
- that wrapper registers `submit_judge_verdict`
- benchmark runner reads the verdict from the tool payload first and only falls back to prose JSON extraction if the tool payload is absent

Validation status:

- direct runtime smoke for the custom `benchmark_judge` RPC profile passed
- benchmark smoke at `/private/tmp/syke-bench-judge-native-r01` passed
- background validation batch over `R01..R05 × pure/syke/zero` landed `14/15` verdicts through `submit_judge_verdict`
- that batch saw `0` judge refusals, `0` judge timeouts, and `1` provider-side invalid (`server_error` before tool call)

Interpretation:

- the old judge JSON-formatting problem is now effectively solved
- remaining invalids are transport/runtime, not "agent could not format JSON"

## Apr 14-15 update — ask side

The ask side is still an open problem and should be treated separately from judge-path work.

Current understanding:

- benchmark ask failures are still a mix of refusal-shaped outputs, one termination/drop case, and grounded wrong answers
- live product ask failures are a different class, dominated by `fetch failed` and timeout/runtime failures
- broad benchmark prompts like "list everything", "all my current open threads", and "what happened in the last week across all sessions/projects" often do substantial work first, then refuse late
- so the remaining ask issue is not a JSON problem; it looks more like an alignment/safety interaction in the benchmark environment plus some runtime fragility

## Interpretation caveat

The current benchmark path is strong enough for local analysis, but it still has one important methodological caveat:

- raw benchmark slices and git cutoffs use exact ask-local timestamps
- replay memex matching is still day-granular

So same-day ask evaluation is not yet full intraday replay fidelity.
