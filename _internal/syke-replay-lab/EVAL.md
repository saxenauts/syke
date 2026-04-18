# Eval — Taxonomy, Data Model, Visualization

Canonical reference for the Syke eval system. Read this before touching anything eval-related.

If you need the shortest semantic statement of the harness itself, start with [ENVIRONMENT_CONTRACT.md](/Users/saxenauts/Documents/personal/syke/_internal/syke-replay-lab/ENVIRONMENT_CONTRACT.md). This file focuses on taxonomy, packets, and runner behavior.

The eval layer exists to answer one question: **does Syke's stateful memory actually help an agent reconstruct the user's changing project world at a past reference time, compared to not having it?** Everything else (probe sets, runs, packets, viz) serves that question.

---

## Taxonomy

```
Probe       a question with a reference timestamp. Stable identity (R01..Rn).
Condition   a named experimental variant ("pure", "syke", "zero",
            "syke-minimax", "syke+prompt-v2", …). Just a string. N unbounded.
Rollout     one (probe × condition) evaluation — produces ask + judge + artifacts.
Run         a directory on disk from one benchmark_runner invocation.
            Contains benchmark_results.json + evidence/ + traces/.
Packet      a declarative eval view — a named set of (condition, source_run)
            pairs, composed by eval_viz.html at load time. No new artifacts
            on disk, just an entry in runs/eval_manifest.json.
```

Conditions are strings. Adding a new condition (say `syke-deepseek`) is just the name of an ask strategy + wherever its rollouts got written.

---

## Judge Output Contract

The benchmark judge is agentic, but its final verdict must be structured.

Current canonical path:

- `transport="benchmark_judge"` routes through a dedicated Pi runtime profile
- that profile is still RPC-based, but it is **Syke-owned RPC**, not stock `pi --mode rpc`
- the wrapper registers a typed Pi SDK tool: `submit_judge_verdict`
- benchmark runner gives the judge only the neutral evaluator prompt + packet/slice/git anchor context
- benchmark runner reads the tool payload first
- prose JSON extraction remains only as fallback

Why this matters:

- prompted "return ONLY valid JSON" is not a real completion contract
- stock Pi RPC exposes prompt text + typed events, but not provider-style final `json_schema`
- Pi-native structure lives at the SDK/custom-tool layer

So: keep the judge agentic, keep Pi, and finish through the typed verdict tool.

Canonical verdict shape lives in `benchmark_runner.py` and
`probes/JUDGE_METHOD_V1.md`. Do not add or revive a parallel schema file under
the replay-lab root.

---

## The `pure` baseline

`pure` is **the** null baseline condition. It keeps only the static identity/world-model block and the frozen workspace evidence at the reference cutoff. There is no injected memex block and no synthesis/control block. Every eval packet should include it as the reference for "what can you recover without Syke state at all". The viz still hard-codes the condition name `pure` as the leftmost column in the matrix and the first peer card in detail — with a `BASE` tag anchoring it.

This is the only place the viz is opinionated about condition names. Everything else is N-agnostic.

---

## Probes

Probes live in `probes/`. Each probe specifies:

- `probe_id` — stable identity (R01, R02, …)
- `reference_ts_local` + `reference_cutoff_iso` — the "as-of" moment
- `prompt_text` — the question asked verbatim
- `source_ref` — provenance pointer (original transcript line)

Adding a probe: edit the probe-set YAML (`probes/REAL_ASK_RUNSETS.yaml` or similar), rerun whichever runs contain it. `probe_id` is stable across runs — once assigned, it is the identity.

---

## Conditions

In the current code:

| name   | description |
|--------|-------------|
| `pure` | Null baseline. Static identity/world model only, plus frozen workspace evidence. No memex block and no synthesis block. |
| `production` | Full ask stack. Static identity + memex + synthesis/control block. |
| `zero` | Substrate-only ablation. Static identity + memex, but no synthesis/control block. |

Other replayed conditions are allowed too. The invariant is not a small fixed list; it is that eval condition names must exactly match the replay source's `metadata.condition`, and syke-mode eval reuses the replay source's recorded skill content.

Adding a condition:

1. Replay the condition first so the replay source itself carries `metadata.condition` and `skill_content`.
2. Run the benchmark against that replay source — the eval condition must exactly match the replay condition.
3. `pure` is always included automatically as the null baseline.
4. Eval reuses the replay source's recorded skill content for syke-mode conditions, so ask behavior cannot silently switch at benchmark time.
5. Reference it in a packet if you want a curated view (see below).

The viz picks up the new condition automatically. The viz hard-codes **only `pure`** as special.

---

## Runs

A run is what `benchmark_runner.py` produces. Directory layout:

```
runs/<run_name>/
  benchmark_results.json       list of items (rollouts)
  run_manifest.json            optional — per-condition summary stats
  probe_matrix.json            optional — probe × condition verdict grid
  config.json                  runner config (probes, conditions, models, merge provenance)
  evidence/<cond>/<probe>/
    packet.json                probe + answer snapshot
    ask_response.txt
    ask_metadata.json
    ask_trace.json             ask transcript (new runs — pi_ask persists via capture_trace=True)
    judge_response.txt         raw judge LLM output
    judge_result.json          parsed verdict
    judge_metadata.json
    local_git_anchor.json      git state at reference_ts (commits, tags, reflog)
    slice/                     replay slice (harness session files, adapters, meta)
  traces/<cond>/
    <cond>_<probe>.ask_trace.json
    <cond>_<probe>.ask_response.txt
    <cond>_<probe>.judge_*
```

Runs with `_merged` suffix are post-hoc aggregations of multiple source runs into one `benchmark_results.json` via the merge tool. **Prefer packets over merged runs for new work** — packets preserve source-run identity per rollout, merged runs don't.

### Where runs land

`benchmark_runner.py` defaults `--output-dir` to
`_internal/syke-replay-lab/runs/<slug>-<UTC-stamp>/` when the flag is omitted.
`<slug>` is the runset name, else `items-N`, else `run`.

**Do not write runs to `/private/tmp/` or elsewhere outside the lab.** The
eval viz auto-discovers anything under `runs/` that has a
`benchmark_results.json`. Declared packets from `runs/eval_manifest.json` are
loaded first and remain the place for curated views.

Pass `--output-dir` explicitly when you want a specific run name. Add a packet
entry when you want a curated name, description, or composition across runs.

Judge runs on the current path also write:

- `judge_trace.json` — full judge trace payload, including the `submit_judge_verdict` tool call when present

---

## Packets — the compositional unit

A packet is a declarative eval view. Declared in `runs/eval_manifest.json`. Two flavors:

### Single-run packet
```json
{
  "name": "ne13_15d_timefix_baseline_gpt54",
  "description": "Current trusted baseline packet",
  "created_at": "2026-04-17T03:22:38.652270+00:00",
  "path": "./runs/ne13_15d_timefix_baseline_gpt54_20260416T171500Z"
}
```
Loads the run's `benchmark_results.json` as-is. Every item's `_source_path` is set to the run path so evidence/trace/slice fetches always resolve to that run.

**You don't need a packet entry for most runs.** The viz auto-discovers any
`runs/<dir>/` with a `benchmark_results.json`, pulling `config.json`'s
`started_at` for ordering. Add a packet entry when you want a curated label or
composition.

### Composed packet
```json
{
  "name": "model_showdown",
  "description": "pure baseline vs three memex backends",
  "created_at": "2026-04-18T09:00:00Z",
  "conditions": [
    { "name": "pure",         "source": "./runs/baseline" },
    { "name": "syke-gpt-5.4", "source": "./runs/gpt54" },
    { "name": "syke-minimax", "source": "./runs/minimax" },
    { "name": "syke-deepseek","source": "./runs/deepseek" }
  ]
}
```
At load time the viz fetches each source's `benchmark_results.json`, filters items to the declared condition (by default `cond.name`; override with `source_condition` if the name in source differs from the packet alias), and composes them into one view.

Each composed rollout knows its source run, so evidence/trace/slice fetches always resolve.

### Ordering
`created_at` is an optional ISO timestamp. The viz sorts packets descending by it, so the newest becomes the default selection in the picker. Entries without `created_at` sort to the bottom in manifest order. Set it when you add a packet you want to land on first.

### Why packets
- No merge step — no artifact duplication.
- Evidence paths always resolve — each rollout knows its source.
- Add a new experimental condition = add a `{name, source}` entry.
- Easy to swap the baseline or compare different-model `syke` variants.

---

## Adding things

**A new probe** — edit the probe-set YAML, rerun any run that includes it.

**A new condition** — add ask strategy in `benchmark_runner.py`, run it, reference in a packet.

**A new packet** — add an entry to `runs/eval_manifest.json`, refresh `eval_viz.html`.

**A new numbered ablation packet** — use `manage_eval_packets.py` to suggest a
run slug like `ab03-meta-postcheck-eval-<UTC-stamp>` and to upsert the packet
entry in `runs/eval_manifest.json`.

**Share a view** — `eval_viz.html#<packet-name-substring>` auto-selects the matching packet.

---

## Visualizations

Two pages under `_internal/syke-replay-lab/`:

### `replay_viz.html` — Sandbox
Interactive view of **one replay run** (memex evolving day-by-day).
- Top bar: run picker, day scrubber.
- Tabs: Replay (memex / prompt / dashboard), Memory (block grid), Trace (synthesis transcript).
- Accent: purple.

### `eval_viz.html` — Eval
Interactive view of **eval packets**.
- Top bar: packet picker + Matrix/List toggle.
- Summary: condition chips (toggle on/off; pure locked as baseline).
- Matrix view: probes × conditions grid, pure anchored with `BASE` label. Click a cell for detail.
- List view: flat rollout list, filterable by condition.
- Detail panel: question → peers → **Trace** (ask transcript as turn cards with thinking/tool_use/tool_result/text blocks) → **Judge** (axes with score bars, overall summary) → **Evidence** (judge raw response, local git anchor commits) → **Slice** (per-harness-surface counts) → **Artifacts** (resolved paths).
- Accent: amber (NERV / Evangelion aesthetic — geometric, mono-dominant, `◆` baseline marker).

Both pages cross-link via the `sandbox · eval` nav in the top-left.

### Keyboard shortcuts — `eval_viz.html`
- `V` — toggle Matrix / List view.
- `1-9` — switch to the Nth visible condition for the currently selected probe.
- `←` / `→` — prev/next rollout.

---

## Data flow cheat sheet

```
probe definition (probes/*.yaml)
  ↓
benchmark_runner.py                ← add new conditions here
  ↓
runs/<run_name>/
  benchmark_results.json + evidence/ + traces/
  ↓
runs/eval_manifest.json            ← declare packets here
  ↓
eval_viz.html
  ↓
rendered view (matrix or rollout list + detail panel)
```

Two invariants:
1. `probe_id` is stable. Once R07 is defined, R07 is R07 forever.
2. Each rendered rollout knows its source run (via `_source_path`), so every fetch into `evidence/` / `traces/` resolves cleanly regardless of packet composition.

---

## What lives where

```
_internal/syke-replay-lab/
  materialize_bundle.py       build self-contained bundles from raw harness data
  memory_replay.py            replay a bundle into an isolated workspace
  benchmark_runner.py         run probes × conditions → produce a run
  benchmark_scorer.py         reduce judge verdicts → counts / success rates
  memory_replay.py            replay synthesis day-by-day
  probes/                     probe-set YAMLs + judge + scoring method notes
  bundles/                    checked-in bundles (source harness data slices)
  runs/                       all run outputs (gitignored) + eval_manifest.json
  replay_viz.html             sandbox viz
  eval_viz.html               eval viz
  EVAL.md                     this file
  LAB.md                      orientation doc
  README.md                   lab overview + common commands
  QUICK_REFERENCE.md          recipes
```

`runs/` is gitignored. `runs/eval_manifest.json` is the exception — check it in to version the set of active packets.

---

## Failure Split

Keep these classes separate in analysis and reporting:

### Judge transport / structure

- old issue: prose JSON / parser invalids
- current state: largely fixed by the Pi-native `submit_judge_verdict` path

### Ask runtime / refusal

- still open
- broad benchmark asks can refuse late after substantial evidence gathering
- live install failures are mostly transport/runtime (`fetch failed`, timeout), not benchmark-style refusals

### Actual memory quality

- stale prior / wrong anchoring / weak reconstruction
- this is the only class that should count as memory-system quality

Do not collapse these three into one score narrative.
