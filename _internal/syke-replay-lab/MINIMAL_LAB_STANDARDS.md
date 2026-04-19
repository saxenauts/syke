# Minimal Lab Standards

The question this document answers is:

**What is the minimum professional standard Syke Lab needs in order to run many replay/eval experiments without wasting time, while staying replay-native and KISS?**

This is not a framework replacement doc.
This is a standards doc for the lab we already have.

---

## 1. Core Workflow Standard

The workflow must remain:

`Bundle -> Slice -> Replay run -> Rollout -> Run -> Packet`

Definitions:

- `Bundle`
  Frozen transport package of source evidence.

- `Slice`
  Time-bounded evidence surface derived from a bundle at one cutoff.

- `Replay run`
  State evolution across one or more slices.

- `Rollout`
  One `(probe x condition)` evaluation.

- `Run`
  One replay or benchmark invocation directory.

- `Packet`
  A declarative composed eval view across runs.

This workflow is the lab's source of truth.

---

## 2. Scientific Contract Standard

Every experiment must preserve the environment contract:

- bounded evidence only
- same underlying slice reality across conditions
- explicit as-of time for ask and judge
- no live global state contamination
- no judged artifacts from outside the bounded run

If this fails, the run is not scientifically interpretable.

See:
- [`ENVIRONMENT_CONTRACT.md`](/Users/saxenauts/Documents/personal/syke/_internal/syke-replay-lab/ENVIRONMENT_CONTRACT.md)

---

## 3. Artifact Standard

Every phase must emit stable, resumable artifacts.

### Replay artifacts

Required:

- `replay_results.json`
- replay workspace
- cycle slices
- memex snapshots

### Benchmark artifacts

Required:

- `results.json`
- `benchmark_results.json`
- `config.json`
- `evidence/<condition>/<probe>/...`
- `traces/<condition>/...`

### Judge-only rerun artifacts

Required:

- fresh output directory
- copied ask-side artifacts
- new judge-side artifacts
- explicit provenance in `config.json`

---

## 4. Checkpoint Standard

Every long-running phase must support restart from disk.

Minimum:

- status
- partial/completed flag
- completed work count
- last successful unit of work
- failure reason
- heartbeat timestamp

Replay already does this better than benchmark.
Benchmark should reach the same standard.

---

## 5. Run Management Standard

The lab needs a thin run manager.

It does **not** need to become Airflow, Temporal, or a hosted control plane.

Minimum required responsibilities:

- phase-aware run registry
  - replay
  - benchmark
  - judge-only

- dependency tracking
  - benchmark depends on replay
  - judge-only depends on ask artifacts

- resumability
  - know what is safe to resume
  - know what is safe to rerun

- stale-run detection
  - dead process
  - no heartbeat
  - no progress over threshold

- failure classification
  - provider/model mismatch
  - runtime/tool failure
  - judge schema failure
  - contamination failure

---

## 6. Parallelism Standard

Parallelism must be explicit and provider-aware.

Minimum:

- max concurrency per provider
- max concurrency per model
- queue by phase
- queue by dependency
- retry policy by failure class

The standard is not "as much parallelism as possible."
The standard is "safe, explainable, resumable parallelism."

---

## 7. Progress and ETA Standard

Runs must be visible while live.

Minimum live information:

- run id
- phase
- provider/model
- completed units / total units
- current rate
- ETA
- last heartbeat
- current failure state if any

This should become visible in the lab UI, not just shell logs.

---

## 8. Provenance Standard

Every result must preserve lineage.

Minimum:

- which bundle it came from
- which slice/cutoff it used
- which replay run produced the state
- which condition was evaluated
- which judge metric/schema version was used
- whether any rows were repaired or rerun

This is non-negotiable if results are going to be shown to labs.

---

## 9. Failure Taxonomy Standard

Failure modes must become first-class artifacts.

Minimum fields:

- id
- short label
- class
  - contamination
  - time
  - state selection
  - stale prior
  - judge contract
  - runtime/provider
- description
- affected conditions
- example episodes
- fix status

This is how the lab becomes cumulative rather than anecdotal.

---

## 10. Standard-Facing Language

Use industry-facing aliases only where they help external readers.

Recommended:

- `Bundle` -> `dataset snapshot`
- `Slice` -> `evidence slice`
- `Replay run` -> `state rollout`
- `Run` -> `experiment run`
- `Packet` -> `benchmark pack`

Do **not** rename the internal core around generic framework language.

The replay-native ontology stays ours.

---

## 11. Libraries Standard

Use small boring libraries where they remove obvious custom work.

Recommended:

- `pydantic`
  For typed schemas/contracts.

- `polars`
  For run/result tables and comparisons.

- `orjson`
  For artifact IO.

- `scipy`
  For experiment statistics.

- `statsmodels`
  For more formal statistical testing when needed.

Avoid adopting a full external framework as the source of truth for the lab
until the replay-native core model is stable.

---

## 12. KISS Boundary

What stays custom:

- replay semantics
- slice generation
- continuity-specific conditions
- failure taxonomy
- packet model
- provenance and repair history

What should become standardized:

- schemas
- tables
- stats
- run metadata
- progress / ETA surface

---

## 13. Immediate Next Standards To Implement

In order:

1. `ReplayReference` object shared across replay / benchmark / judge
2. thin `RunManager`
3. provider-aware scheduler
4. uniform checkpoint schema
5. live run/ETA dashboard
6. first-class failure registry
7. typed schemas for core artifacts

That is the minimum professional standard for Syke Lab.
