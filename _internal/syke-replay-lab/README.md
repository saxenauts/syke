# Replay Lab

Local harness for running, replaying, and evaluating Syke against bundled harness data.

The lab has two surfaces:

- **Replay** — reconstruct memex day-by-day from bundled harness data. Sandbox for watching the stateful machine evolve.
- **Eval** — run probes × conditions against replay outputs, produce verdicts, compare conditions side-by-side.

Both surfaces have their own visualization page. See `EVAL.md` for the eval taxonomy, `LAB.md` for deeper orientation, `MINIMAL_LAB_STANDARDS.md` for the minimum professional standard we want the lab to meet, and `RUN_MANAGER_DESIGN.md` for the orchestration design.

---

## Files

```
materialize_bundle.py       build a self-contained bundle from raw harness files
memory_replay.py            replay a bundle → isolated ~/.syke/-shaped workspace
benchmark_runner.py         run probes × conditions → produce a run directory
benchmark_scorer.py         reduce judge verdicts → per-condition counts / success rates
labctl.py                   thin run manager for replay / benchmark / judge-only phases
manage_eval_packets.py      suggest ablation run names + upsert composed eval packets
probes/                     probe-set YAMLs, judge method notes, scoring method notes
bundles/                    checked-in bundles (slices of raw harness data)
runs/                       run outputs (gitignored) + eval_manifest.json
replay_viz.html             sandbox viz (replay run scrubber, memex evolution, trace)
eval_viz.html               eval viz (packet picker, matrix/list, per-rollout detail)
EVAL.md                     eval taxonomy + packet model + viz architecture
LAB.md                      lab orientation
QUICK_REFERENCE.md          command recipes
```

---

## Taxonomy (one-screen summary)

```
Probe       a question with a reference timestamp (R01..Rn). Stable identity.
Condition   a named ask variant (`pure` null baseline / `syke` full stack /
            `zero` substrate-only / `syke-minimax` / …). Just a string.
Rollout     one (probe × condition) evaluation → ask + judge + artifacts.
Run         a directory from one benchmark_runner invocation (many rollouts).
Packet      a declarative eval view composed from run(s). Lives in eval_manifest.json.
```

Full detail: `EVAL.md`.

---

## Source vs Generated

Treat the replay lab as two different surfaces:

- **Tracked source** — runner code, replay code, viz pages, docs, tests, and
  declarative manifests. These are intended to live in the main repo history.
- **Generated output** — `runs/*` artifacts, evidence, traces, and materialized
  bundles. These stay ignored unless a result package is intentionally promoted
  into a committed canonical artifact.

Concretely:

- Source files under `_internal/syke-replay-lab/` should be tracked normally by
  the main repo.
- `runs/` remains generated-local by default, except for the small declarative
  manifests `runs/eval_manifest.json` and `runs/manifest.json`.
- `bundles/` remains generated/local research state.
- Canonical committed result packages are exceptions and should be kept to the
  smallest trusted set exposed through `runs/eval_manifest.json`.

If a future change requires `git add -f` for ordinary replay-lab source files,
the ignore policy has drifted again and should be fixed before more code lands.

---

## Replay workflow

Each replay creates an isolated workspace with the same flat layout Syke uses in production:

- `syke.db` — single database for memex, memories, links, cycle records.
- `MEMEX.md` — current memex produced by synthesis.
- `PSYCHE.md` — runtime identity file from installed adapters.
- `adapters/` — bundle-provided adapter markdown.
- `sessions/` — runtime session dir.

```bash
# Build a bundle
python _internal/syke-replay-lab/materialize_bundle.py \
  --window 2026-01-17:2026-02-22 \
  --tag golden-gate-v2 \
  --output _internal/syke-replay-lab/bundles/golden-gate-v2

# Replay (production condition = full syke memex)
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/ne-1.1 \
  --output-dir _internal/syke-replay-lab/runs/ne_1_1_run \
  --user-id replay \
  --start-day 2026-01-09 \
  --max-days 5 \
  --condition production

# Dry-run (skip synthesis)
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/ne-1.1 \
  --output-dir _internal/syke-replay-lab/runs/ne_1_1_dry_run \
  --dry-run
```

Outputs under `--output-dir`:
- `replay_results.json` — per-cycle record of synthesis behavior.
- `workspace/` — final replayed workspace state.
- `memex/` — versioned memex snapshots.

---

## Eval workflow

```bash
# Run probes × conditions against a replay.
# --output-dir is optional; defaults to
# _internal/syke-replay-lab/runs/<slug>-<UTC-stamp>/
python _internal/syke-replay-lab/benchmark_runner.py \
  --runset real_ask \
  --replay-dir production:_internal/syke-replay-lab/runs/ne13_prod_codex54mini_timefix_20260416T142500Z \
  --replay-dir zero:_internal/syke-replay-lab/runs/ne13_zero_codex54mini_timefix_20260416T142500Z
```

`pure` is always included automatically as the null baseline. `zero` keeps Syke substrate but drops the synthesis/control block. Any non-baseline eval condition is allowed, but it must match the replay source's own `metadata.condition` exactly, and eval reuses that replay source's recorded skill content so the ask side does not silently switch.

Outputs under `runs/<name>/`:
- `benchmark_results.json` — every rollout's probe + answer + judge.
- `config.json` — runner config + `started_at`; the viz uses this for ordering.
- `evidence/<cond>/<probe>/` — per-rollout evidence (ask trace, judge response, git anchor, slice).
- `traces/<cond>/` — flat copies of ask/judge traces per rollout.

Runs surface automatically in the eval viz when written under `runs/`. Use `runs/eval_manifest.json` when you want a curated packet name, description, or a composed cross-run view.

Do **not** write runs under `/private/tmp/` or anywhere outside the lab — the viz can only serve paths under `_internal/syke-replay-lab/` via its static HTTP server.

If only the judge changed, reuse existing ask outputs instead of paying ask cost again:

```bash
python _internal/syke-replay-lab/benchmark_runner.py \
  --runset ne13_real_15d \
  --judge-only-from _internal/syke-replay-lab/runs/ne13_15d_timefix_baseline_gpt54_20260416T171500Z \
  --output-dir _internal/syke-replay-lab/runs/ab06-judge-only-rerun \
  --judge-model gpt-5.4
```

For cross-run comparison, declare a composed packet instead:

```json
{
  "name": "model_showdown",
  "conditions": [
    { "name": "pure",         "source": "./runs/baseline" },
    { "name": "syke-gpt-5.4", "source": "./runs/gpt54" },
    { "name": "syke-minimax", "source": "./runs/minimax" }
  ]
}
```

See `EVAL.md` for the full packet spec.

For a helper that suggests ablation-numbered run names and writes packet entries
into `runs/eval_manifest.json`, use:

```bash
python _internal/syke-replay-lab/manage_eval_packets.py suggest-run-name \
  --ablation 3 \
  --label meta-postcheck \
  --kind eval

python _internal/syke-replay-lab/manage_eval_packets.py upsert-packet \
  --ablation 3 \
  --label meta-postcheck \
  --description "pure + production + meta-postcheck comparison" \
  --condition pure=_internal/syke-replay-lab/runs/ab03-pure-eval-20260418T010203Z \
  --condition production=_internal/syke-replay-lab/runs/ab03-production-eval-20260418T010203Z \
  --condition syke_meta_postcheck=_internal/syke-replay-lab/runs/ab03-meta-postcheck-eval-20260418T010203Z
```

For lightweight orchestration across replay / benchmark / judge-only phases, use:

```bash
python _internal/syke-replay-lab/labctl.py submit-replay ...
python _internal/syke-replay-lab/labctl.py submit-benchmark ...
python _internal/syke-replay-lab/labctl.py tick
python _internal/syke-replay-lab/labctl.py status
```

---

## Visualizations

Both pages are static HTML — open them directly, or serve the lab dir with any static HTTP server:

```bash
cd _internal/syke-replay-lab && python3 -m http.server 8742
# open http://localhost:8742/replay_viz.html
# open http://localhost:8742/eval_viz.html
```

- `replay_viz.html` — pick a replay run, scrub through days, watch memex / memory / trace evolve.
- `eval_viz.html` — pick a packet, see the probes × conditions matrix, drill into any rollout for ask trace / judge reasoning / evidence / slice.

The two pages cross-link via the `sandbox · eval` nav in the top-left.

---

## Available bundles

Current bundle directories under `bundles/`:

`golden-gate-v2`, `ne-1.1`, `ne-1.2`, `ne-1.3`, `pure-syke`, `s07-cross-harness`, `s08-post-spike`.

---

## For future agents working in here

1. Read `EVAL.md` first. It's the single source of truth for taxonomy and packet conventions.
2. Probes are stable. `probe_id` is identity — don't renumber.
3. Conditions are strings — add them freely. Only `pure` is hard-coded as the baseline column in the viz.
4. Prefer **composed packets** over creating new merged runs. Packets preserve source-run identity; merged runs throw it away.
5. The viz is static HTML fetching local JSON — no server-side logic. If you need new views, extend `eval_viz.html` / `replay_viz.html` directly or add sibling pages.
6. When running experiments, write traces (`pi_ask(..., capture_trace=True)` is already the default in `benchmark_runner._ask_probe`). The eval viz renders them as turn cards.
