# Experiments

Working material for local evaluation and iteration on the current Syke branch. This directory is for trying ideas against the real codebase; it is not stable product surface.

## What `experiments/` Contains

A few different kinds of repo-local evaluation material live here:

- `memory_replay.py` — the main current replay harness
- `prompts/` — prompt variants and ablation prompt files
- `runs/` and `results/` — saved outputs from prior runs
- `benchmarking/`, `simulation/`, `viz/` — additional working material and analysis tools

If you only need one place to start, start with replay.

## Replay Is the Main Current Evaluation Path

`memory_replay.py` replays a frozen event database through synthesis one day at a time, starting from a fresh replay DB, and writes out:

- a replay database for that run
- a `replay_results.json` timeline
- versioned memex snapshots under `memex/`

For the fuller operator view, see [docs/RUNTIME_AND_REPLAY.md](../docs/RUNTIME_AND_REPLAY.md).

## Prompt Files

Prompt files under `experiments/prompts/` are plain text overrides passed with `--skill`.

Common examples:

- `experiments/prompts/minimal.md` — shortest useful prompt
- `experiments/prompts/zero.md` — empty prompt
- `experiments/prompts/single_doc.md` — rewrite the memex document only

There are also built-in replay conditions in `memory_replay.py`:

- `production`
- `no_pointers`
- `neutral`

`--skill` overrides the condition and the normal synthesis skill file.

## Run Outputs

A replay run writes to the directory you pass as `--output-dir`.

Expect to inspect:

- `replay_results.json`
- `replay.db`
- `memex/vNNN.md`

Replay also updates shared run bookkeeping in `experiments/runs/manifest.json` and `experiments/experiments.db`.

## Copy-Pasteable Commands

Use the frozen local dataset unless you are intentionally replaying a different DB.

### 1. Dry run

```bash
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir experiments/runs/local_dry_run \
  --user-id replay_dry \
  --source-user-id fresh_test \
  --dry-run
```

### 2. Short Pi run

```bash
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir experiments/runs/local_pi_3d \
  --user-id replay_pi_3d \
  --source-user-id fresh_test \
  --model qwen3-coder \
  --max-days 3 \
  --skill experiments/prompts/minimal.md
```

### 3. Start-day run

```bash
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir experiments/runs/local_feb_window \
  --user-id replay_feb_window \
  --source-user-id fresh_test \
  --start-day 2026-02-01 \
  --max-days 5
```

That last command is the current practical substitute for “resume a later window”: start fresh from a chosen day and inspect that slice.
