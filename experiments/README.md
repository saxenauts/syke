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

Important semantics:

- replay days are distinct observed `DATE(timestamp)` values for the selected source user
- `--max-days` limits observed days, not contiguous calendar days
- the selected replay window can be much smaller than the underlying frozen DB
- concrete local dataset names, user IDs, and date ranges should stay out of tracked docs

## Prompt Files

Prompt files under `experiments/prompts/` are plain text overrides passed with `--skill`.

Common examples:

- `experiments/prompts/minimal.md` — shortest useful prompt
- `experiments/prompts/zero.md` — empty prompt
- `experiments/prompts/single_doc.md` — rewrite the memex document only
- `experiments/prompts/balanced_pi.md` — balanced Pi-native replay prompt for the current runtime

There are also built-in replay conditions in `memory_replay.py`:

- `production`
- `no_pointers`
- `neutral`

`--skill` overrides the condition and the normal synthesis skill file.

Important:

- `experiments/prompts/balanced.md` is a legacy Claude-era tool-contract prompt. It references `memory_write`, `search_memories`, and `commit_cycle`, which do not exist in Pi runtime.
- For current Pi replay experiments, prefer `balanced_pi.md` or another Pi-native prompt.

## Run Outputs

A replay run writes to the directory you pass as `--output-dir`.

Expect to inspect:

- `replay_results.json`
- `workspace/syke.db`
- `workspace/events.db`
- `memex/vNNN.md`

Replay also updates shared run bookkeeping in `experiments/runs/manifest.json` and `experiments/experiments.db`.

Each run creates an isolated Pi workspace under `--output-dir/workspace`. For replay, the canonical run-local DB is `workspace/syke.db`, and `workspace/events.db` is the readonly evidence snapshot Pi reads during the run.

## Copy-Pasteable Commands

Use your local frozen dataset unless you are intentionally replaying a different DB.

### 1. Dry run

```bash
SOURCE_DB=/path/to/local/frozen_replay.db
SOURCE_USER=source_user

python experiments/memory_replay.py \
  --source-db "$SOURCE_DB" \
  --output-dir experiments/runs/local_dry_run \
  --user-id replay_dry \
  --source-user-id "$SOURCE_USER" \
  --dry-run
```

### 2. Short Pi run

```bash
SOURCE_DB=/path/to/local/frozen_replay.db
SOURCE_USER=source_user

python experiments/memory_replay.py \
  --source-db "$SOURCE_DB" \
  --output-dir experiments/runs/local_pi_3d \
  --user-id replay_pi_3d \
  --source-user-id "$SOURCE_USER" \
  --model qwen3-coder \
  --max-days 3 \
  --skill experiments/prompts/balanced_pi.md
```

### 3. Start-day run

```bash
SOURCE_DB=/path/to/local/frozen_replay.db
SOURCE_USER=source_user

python experiments/memory_replay.py \
  --source-db "$SOURCE_DB" \
  --output-dir experiments/runs/local_feb_window \
  --user-id replay_feb_window \
  --source-user-id "$SOURCE_USER" \
  --start-day 2026-02-01 \
  --max-days 5
```

Replay does not currently support resume or append mode. That last command is the practical substitute for “resume a later window”: start fresh with a new `--output-dir` from a chosen day and inspect that slice.

### 4. 31-observed-day run

```bash
SOURCE_DB=/path/to/local/frozen_replay.db
SOURCE_USER=source_user

python experiments/memory_replay.py \
  --source-db "$SOURCE_DB" \
  --output-dir experiments/runs/local_pi_31d \
  --user-id replay_pi_31d \
  --source-user-id "$SOURCE_USER" \
  --max-days 31 \
  --skill experiments/prompts/balanced_pi.md
```

This selects the first 31 observed days from the chosen source slice. It does not imply that the underlying frozen DB contains only 31 days of data.
