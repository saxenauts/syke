# Replay Lab Quick Reference

## Bundle build

Create a bundle:

```bash
python _internal/syke-replay-lab/materialize_bundle.py \
  --window 2026-01-17:2026-02-22 \
  --tag golden-gate-v2 \
  --output _internal/syke-replay-lab/bundles/golden-gate-v2
```

Limit sources during bundle creation:

```bash
python _internal/syke-replay-lab/materialize_bundle.py \
  --window 2026-03-01:2026-03-16 \
  --tag ne1-march \
  --output _internal/syke-replay-lab/bundles/ne1-march \
  --sources claude-code,codex,hermes
```

## Replay commands

Dry run a bundle:

```bash
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/ne-1.1 \
  --output-dir _internal/syke-replay-lab/runs/ne_1_1_dry_run \
  --dry-run
```

Replay a few days:

```bash
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/ne-1.1 \
  --output-dir _internal/syke-replay-lab/runs/ne_1_1_run \
  --user-id replay \
  --start-day 2026-01-09 \
  --max-days 5 \
  --condition syke
```

Replay with multiple cycles per day:

```bash
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/golden-gate-v2 \
  --output-dir _internal/syke-replay-lab/runs/golden_gate_multi \
  --cycles-per-day 3
```

Replay with a custom skill file:

```bash
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/ne-1.2 \
  --output-dir _internal/syke-replay-lab/runs/ne_1_2_custom_skill \
  --skill /absolute/path/to/skill.md
```

Replay with provider overrides:

```bash
python _internal/syke-replay-lab/memory_replay.py \
  --bundle _internal/syke-replay-lab/bundles/ne-1.3 \
  --output-dir _internal/syke-replay-lab/runs/ne_1_3_provider \
  --provider azure-openai-responses \
  --api-key "$AZURE_OPENAI_API_KEY" \
  --base-url https://example.openai.azure.com/openai/v1 \
  --model gpt-5.4-mini
```

## Replay flags

`--bundle`: required bundle directory.

`--output-dir`: required replay output directory.

`--user-id`: external user id recorded in replay metadata. Default: `replay`.

`--dry-run`: print selected days and cycles without running synthesis.

`--max-days`: limit selected replay days after any `--start-day` filter.

`--start-day`: start from the first bundle day greater than or equal to the given date.

`--cycles-per-day`: split each selected day into sequential replay cycles. Default: `1`.

`--condition`: replay condition. Choices: `syke`, `zero` (`production` is accepted as a backward-compatible alias).

`--skill`: custom skill file path; overrides `--condition`.

`--model`: synthesis model override.

`--provider`, `--api-key`, `--base-url`: provider override settings written into the replay workspace.

Archive an important completed run locally:

```bash
python _internal/syke-replay-lab/archive_run.py \
  --run-dir _internal/syke-replay-lab/runs/ne13_15d_codex54mini_fresh_20260416T082802Z
```

This writes a self-contained local archive bundle under:

- `_internal/syke-replay-lab/_archive/run-packages/<run-name>/`
- `_internal/syke-replay-lab/_archive/run-packages/<run-name>.tar.gz`
- `_internal/syke-replay-lab/_archive/run-packages/<run-name>.tar.gz.sha256`

## Benchmark runner commands

Suggest an ablation-numbered run directory name:

```bash
python _internal/syke-replay-lab/manage_eval_packets.py suggest-run-name \
  --ablation 3 \
  --label meta-postcheck \
  --kind eval
```

Create or replace a composed eval packet from multiple runs:

```bash
python _internal/syke-replay-lab/manage_eval_packets.py upsert-packet \
  --ablation 3 \
  --label meta-postcheck \
  --description "pure + syke + meta-postcheck comparison" \
  --condition pure=_internal/syke-replay-lab/runs/ab03-pure-eval-20260418T010203Z \
  --condition syke=_internal/syke-replay-lab/runs/ab03-production-eval-20260418T010203Z \
  --condition syke_meta_postcheck=_internal/syke-replay-lab/runs/ab03-meta-postcheck-eval-20260418T010203Z
```

Run a named runset:

```bash
python _internal/syke-replay-lab/benchmark_runner.py \
  --runset core_eval \
  --output-dir _internal/syke-replay-lab/results/core_eval
```

Run explicit items:

```bash
python _internal/syke-replay-lab/benchmark_runner.py \
  --item P001 \
  --item P002 \
  --output-dir _internal/syke-replay-lab/results/manual_items
```

Run with replay and judge overrides:

```bash
python _internal/syke-replay-lab/benchmark_runner.py \
  --runset ne13_real_full \
  --output-dir _internal/syke-replay-lab/results/ne13_real_full_gpt54 \
  --replay-dir syke:_internal/syke-replay-lab/runs/production \
  --ask-model gpt-5.4 \
  --judge-model gpt-5.4 \
  --ask-timeout 900 \
  --judge-timeout 900
```

Validate the Pi-native structured judge path on a small batch:

```bash
.venv/bin/python _internal/syke-replay-lab/benchmark_runner.py \
  --output-dir /private/tmp/syke-judge-validate-r01-r05 \
  --item R01 --item R02 --item R03 --item R04 --item R05 \
  --replay-dir syke:_internal/syke-replay-lab/runs/ne13_prod_codex54mini_timefix_20260416T142500Z \
  --replay-dir zero:_internal/syke-replay-lab/runs/ne13_zero_codex54mini_timefix_20260416T142500Z \
  --ask-model gpt-5.4 \
  --judge-model gpt-5.4 \
  --ask-timeout 600 \
  --judge-timeout 900 \
  --jobs 3
```

Run a custom canonical item file directly:

```bash
uv run python _internal/syke-replay-lab/benchmark_runner.py \
  --items-file research/n1-memory-lab/NE_1_3_REAL_ASK_EVAL_SET.yaml \
  --all-items \
  --output-dir _internal/syke-replay-lab/runs/ne13-real-asks \
  --replay-dir syke:_internal/syke-replay-lab/runs/production
```

Run the canonical real-ask runset:

```bash
uv run python _internal/syke-replay-lab/benchmark_runner.py \
  --items-file research/n1-memory-lab/NE_1_3_REAL_ASK_EVAL_SET.yaml \
  --runsets-file _internal/syke-replay-lab/probes/REAL_ASK_RUNSETS.yaml \
  --runset ne13_real_full \
  --output-dir _internal/syke-replay-lab/runs/ne13-real-full \
  --replay-dir syke:_internal/syke-replay-lab/runs/production
```

Rerun only the judge on an existing benchmark run:

```bash
python _internal/syke-replay-lab/benchmark_runner.py \
  --runset ne13_real_15d \
  --judge-only-from _internal/syke-replay-lab/runs/ne13_15d_timefix_baseline_gpt54_20260416T171500Z \
  --output-dir _internal/syke-replay-lab/runs/ab06-judge-only-rerun \
  --judge-model gpt-5.4
```

## Benchmark runner flags

`--runset`: named execution subset from the benchmark runset yaml.

Default runsets now come from `_internal/syke-replay-lab/probes/REAL_ASK_RUNSETS.yaml`.

`--item`: explicit benchmark item id; repeat the flag to add more items.

`--output-dir`: optional output root for benchmark results. If omitted, the runner writes under `_internal/syke-replay-lab/runs/<slug>-<UTC-stamp>/`.

`--replay-dir`: condition:path pair for replay results, or a bare condition like `pure` for the null baseline.

`--ask-model`: ask/runtime model override. If omitted, uses the current Syke Pi default model.

`--judge-model`: judge model name. Default: `gpt-5.4`.

`--ask-timeout`: per-item ask timeout in seconds. Default: `900`.

`--judge-timeout`: per-item judge timeout in seconds. Default: `900`.

`--judge-only-from`: reuse existing answers from a prior benchmark run and rerun
only the judge.

`--items-file`: override the benchmark items YAML.

Default items now come from `research/n1-memory-lab/NE_1_3_REAL_ASK_EVAL_SET.yaml`.

`--runsets-file`: override the benchmark runsets YAML.

`--all-items`: run every item from the selected items YAML.

## Files written

Benchmark runs write:

- `<output-dir>/results.json` — checkpoint rows for resumability
- `<output-dir>/benchmark_results.json` — canonical final artifact
- `<output-dir>/config.json` — run config + provenance
- `<output-dir>/traces/<condition>/` — judge response and metadata traces
- `<output-dir>/evidence/<condition>/<probe_id>/` — persisted evidence pack

On the current judge path, the evidence pack also contains:

- `judge_trace.json` — full Pi trace payload with the `submit_judge_verdict` tool call when successful

## Current bundle directories

`golden-gate-v2`, `ne-1.1`, `ne-1.2`, `ne-1.3`, `pure-syke`, `s07-cross-harness`, `s08-post-spike`.
