# Run Manager Design

Minimal design for the replay/eval orchestration layer.

This document answers:

**How do we run many replay, benchmark, and judge-only experiments safely, in parallel, with checkpoints, ETA, and provenance, without replacing the replay-native core model?**

This is a thin manager around the lab we already have.
It is **not** a workflow platform rewrite.

---

## 1. Design Principle

Keep the existing workflow and file contracts:

`Bundle -> Slice -> Replay run -> Rollout -> Run -> Packet`

The run manager should orchestrate those objects.
It should not redefine them.

So:

- existing runner code stays the execution engine
- the run manager becomes the coordination layer
- replay-native nouns remain the source of truth

---

## 2. Scope

The run manager owns:

- submission
- dependency tracking
- provider-aware scheduling
- process supervision
- progress/heartbeat collection
- ETA
- resumability
- failure classification
- run registry

It does **not** own:

- slicing logic
- replay synthesis logic
- benchmark ask/judge logic
- packet composition semantics

Those remain in:

- `memory_replay.py`
- `benchmark_runner.py`
- `manage_eval_packets.py`

---

## 3. Phases

The manager should treat phases explicitly.

### Replay

Input:
- `Bundle`
- replay config

Output:
- `Replay run`
- `replay_results.json`

### Benchmark

Input:
- replay source(s)
- probe set / runset
- conditions

Output:
- `Run`
- `results.json`
- `benchmark_results.json`

### Judge-only

Input:
- existing benchmark run
- judge config

Output:
- fresh benchmark run with reused ask artifacts

These are different phase types with different progress semantics.

---

## 4. Core Objects

The manager needs only a small number of new orchestration objects.

### ManagedRun

One scheduled or executing job.

Fields:

- `run_id`
- `phase` = `replay | benchmark | judge_only`
- `label`
- `status` = `queued | running | completed | failed | cancelled | stale`
- `created_at`
- `started_at`
- `completed_at`
- `heartbeat_at`
- `owner_cmd`
- `workdir`
- `output_dir`
- `pid`
- `process_group`
- `provider`
- `model`
- `deps`
- `failure_class`
- `resume_supported`
- `metadata`

### ProgressSnapshot

Normalized progress view independent of phase.

Fields:

- `completed_units`
- `total_units`
- `unit_label`
- `rate_per_min`
- `eta_seconds`
- `last_successful_unit`
- `partial`
- `message`

### FailureRecord

Structured failure classification.

Fields:

- `class`
- `summary`
- `detail`
- `retryable`
- `first_seen_at`

---

## 5. Registry

Use one local registry file under the replay lab:

`_internal/syke-replay-lab/runs/run_registry.json`

This is the source of truth for all managed runs.

It should contain:

- top-level metadata/version
- active queue
- all managed runs by `run_id`
- dependency edges

Why JSON:

- simplest possible implementation
- inspectable in gitignored local state
- easy to consume from viz

Optional append-only event log:

`_internal/syke-replay-lab/runs/run_events.jsonl`

This makes debugging and live UI simpler without complicating the registry.

---

## 6. Submission Model

Provide a tiny CLI, e.g.:

- `labctl submit replay ...`
- `labctl submit benchmark ...`
- `labctl submit judge-only ...`
- `labctl status`
- `labctl watch <run_id>`
- `labctl cancel <run_id>`
- `labctl retry <run_id>`

Each submit command:

1. validates inputs
2. computes dependencies
3. writes a `ManagedRun` into the registry
4. enqueues it

The manager does not execute the run inline.

---

## 7. Dependencies

Dependencies must be explicit.

### Replay

No upstream dependency except a `Bundle`.

### Benchmark

Depends on:

- one or more replay outputs for non-`pure` conditions

### Judge-only

Depends on:

- an existing benchmark run with ask artifacts

The manager should never launch a benchmark or judge-only run whose dependencies
are absent or failed.

---

## 8. Scheduling

Scheduling should be provider-aware but minimal.

### Queue Keys

Schedule by:

- phase
- provider
- model

### Limits

Configurable limits:

- max concurrent replays
- max concurrent benchmarks
- max concurrent judge-only runs
- max concurrent runs per provider
- max concurrent runs per provider+model

### Why provider-aware

Because:

- providers have different quotas
- models have different latency
- failures cluster differently by provider/model

This should be configuration, not hard-coded policy.

---

## 9. Process Model

The manager launches the existing scripts as subprocesses.

Examples:

- replay:
  `python _internal/syke-replay-lab/memory_replay.py ...`

- benchmark:
  `python _internal/syke-replay-lab/benchmark_runner.py ...`

- judge-only:
  same benchmark runner with `--judge-only-from`

This is important:

- the run manager should not duplicate runner logic
- it only wraps it

---

## 10. Progress Model

Normalize progress by phase.

### Replay progress

Read from `replay_results.json`:

- `completed_cycles`
- `selected_replay_cycles`
- `partial`
- `status`
- `heartbeat_at`

### Benchmark progress

Read from:

- `results.json`
- `config.json`

Progress:

- completed rollout count
- total rollout count = `len(probes) * len(conditions)`

### Judge-only progress

Read from:

- `results.json`
- `config.json`

Progress:

- completed rerun row count
- total rerun row count

---

## 11. ETA

ETA should be computed from observed progress, not static guesses.

### Primary method

- rate = completed units / elapsed running time
- ETA = remaining units / rate

### Fallback method

Use historical median duration by:

- phase
- provider
- model
- unit count

This allows:

- immediate ETA after launch
- better ETA once enough progress exists

---

## 12. Heartbeat and Staleness

Every managed run needs a heartbeat.

Replay already provides one.

Benchmark/judge-only should be considered alive if:

- subprocess still exists
- and progress files changed recently

Stale if:

- process died unexpectedly
- no heartbeat within threshold
- no file/progress updates within threshold

This is how we stop wasting time on dead runs.

---

## 13. Failure Classes

Standardize failure classes.

Minimum set:

- `dependency_missing`
- `provider_model_invalid`
- `provider_quota`
- `runtime_failure`
- `timeout`
- `judge_contract_failure`
- `contamination_failure`
- `artifact_missing`
- `worker_crash`
- `stale_run`

Each failed run should resolve to one primary class.

---

## 14. Resumability

Do not rerun work that is already checkpointed.

Rules:

- replay resumes from `replay_results.json`
- benchmark resumes from `results.json`
- judge-only resumes from `results.json`

The manager should expose:

- `resume_supported`
- `resume_from`
- `completed_units`

and refuse unsafe resume modes.

---

## 15. Provenance

The manager must preserve lineage.

At minimum:

- replay run used by a benchmark condition
- benchmark run used by a judge-only rerun
- packet composition source runs
- repair provenance

This should be written into run metadata, not reconstructed ad hoc later.

---

## 16. Visualization

Add a lightweight run dashboard, separate from replay/eval detail pages.

Minimum columns:

- run id
- phase
- label
- provider/model
- status
- progress
- ETA
- heartbeat
- deps
- failure class

This can live as:

- `runs_viz.html`

and read:

- `run_registry.json`
- `run_events.jsonl`

No need for a full app framework.

---

## 17. Minimal Build Sequence

Build in this order:

1. `ReplayReference` shared config object
2. `ManagedRun` + registry
3. thin `labctl submit/status/watch/cancel/retry`
4. provider-aware scheduler
5. progress + ETA normalization
6. `runs_viz.html`
7. failure registry integration

That is enough to make the lab operationally professional without replacing the
current replay/eval code.

---

## 18. KISS Boundaries

Do:

- wrap existing runners
- read existing checkpoint files
- add one local registry
- add one event log
- add one run dashboard

Do not:

- build a distributed scheduler
- replace runner internals
- introduce heavy workflow frameworks
- hide artifacts behind a database before the model stabilizes

This is the smallest serious orchestration layer for Syke Lab.
