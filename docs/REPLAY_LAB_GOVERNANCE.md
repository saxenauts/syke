# Replay Lab Governance

This note exists to keep `_internal/syke-replay-lab/` usable without pretending
it is either a normal product surface or a disposable scratchpad.

## What is source

These are the replay-lab surfaces that should be treated as source code or
source documentation:

- runner/replay code
  - `benchmark_runner.py`
  - `memory_replay.py`
  - `benchmark_scorer.py`
  - `cycle_slicer.py`
  - `materialize_bundle.py`
  - `build_local_git_set.py`
- docs and reference notes
  - `README.md`
  - `LAB.md`
  - `EVAL.md`
  - `QUICK_REFERENCE.md`
  - `probes/*.md`
- visualization source
  - `eval_viz.html`
  - `replay_viz.html`
- declarative manifests
  - `runs/eval_manifest.json`
  - `runs/manifest.json`
- replay-lab tests
  - `tests/*.py`

These files are part of the method, not just the output.

## What is generated

These are generated artifacts and should stay local by default:

- ordinary `runs/<name>/` outputs
  - `benchmark_results.json`
  - `results.json`
  - `config.json`
  - `traces/`
  - `evidence/`
- replay outputs
  - `replay_results.json`
  - replayed `workspace/`
  - `memex/`
- materialized `bundles/`

These artifacts are useful for analysis, but they are not source.

## Canonical exceptions

Sometimes a generated artifact becomes a deliberately preserved canonical
package. That should be an explicit exception, not an accidental side effect.

Example:

- `runs/ne13_15d_azmini_merged/`

If a run package is meant to become canonical:

1. make the decision explicitly
2. sanitize it deliberately
3. commit it intentionally

Do not let ordinary local runs drift into that role.

## Commit protocol

The main repo ignores `_internal/` broadly, so replay-lab source changes still
need intentional staging.

Rules:

- treat replay-lab code/docs changes as real source changes
- do **not** commit ordinary generated `runs/` output
- commit canonical result packages only by explicit exception
- when source changes under `_internal/syke-replay-lab/` need to land, stage
  them deliberately with `git add -f`

This is awkward, but it is safer than pretending the entire `_internal/` tree is
product code.

## Operational rule

Use the replay lab like this:

- code/docs/manifests are the method
- runs/bundles are evidence
- only promote evidence into source when we intentionally declare it canonical

That distinction is the minimum governance needed to keep the lab trustworthy.
