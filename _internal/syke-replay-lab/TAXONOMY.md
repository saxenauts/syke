# Replay Lab Taxonomy

Minimal naming for the replay/eval lab.

Use the nouns the repo already uses. Only add industry-facing aliases where
they help external readers. Do not replace the replay-native core model.

---

## Core Workflow

The actual object chain in this repo is:

`Bundle -> Slice -> Replay run -> Rollout -> Run -> Packet`

That is the center of gravity. Everything else should fit around it.

---

## Canonical Terms

### Bundle

Frozen transport package of raw harness evidence plus adapters and metadata.

Industry-facing alias:
- `dataset snapshot`

Repo owners:
- `materialize_bundle.py`
- `bundles/<name>/`

### Slice

Time-bounded evidence subset derived from a bundle at one cutoff.

This is important enough to stay explicit. In this repo, `slice` means the
actual bounded evidence surface, not just a generic research slice.

Industry-facing aliases:
- `evidence slice`
- `episode context`

Repo owners:
- `cycle_slicer.py`
- `slice/` inside evidence packs
- `cycle_slices/` in replay workspaces

### Replay Run

State evolution over one bundle across one or more cycle cutoffs. Writes
`replay_results.json`.

Industry-facing alias:
- `state rollout`

Repo owner:
- `memory_replay.py`

### Probe

One question with a reference timestamp and provenance pointer.

Industry-facing alias:
- `task prompt`

Repo owner:
- `probes/*.yaml`

### Condition

One architecture or control-surface variant under comparison.

Keep the word `condition`.

Repo owner:
- `benchmark_runner.py`
- `EVAL.md`

### Rollout

One `(probe x condition)` evaluation against a bounded replay state. Produces
ask output, judge output, and artifacts.

Keep the word `rollout`.

Repo owner:
- `benchmark_runner.py`
- `results.json`

### Run

One benchmark invocation directory. Contains `benchmark_results.json`,
`results.json`, evidence, and traces.

Industry-facing alias:
- `experiment run`

Repo owner:
- `runs/<run_name>/`

### Packet

A declarative composed eval view across one or more runs.

Industry-facing alias:
- `benchmark pack`

Repo owner:
- `eval_manifest.json`
- `manage_eval_packets.py`

---

## Standard-Facing Mapping

These are the only outer-language mappings we should use for now:

| Repo term | External-facing alias |
|---|---|
| Bundle | Dataset snapshot |
| Slice | Evidence slice |
| Replay run | State rollout |
| Probe | Task prompt |
| Rollout | Evaluation rollout |
| Run | Experiment run |
| Packet | Benchmark pack |

Do not rename these away internally:

- `Bundle`
- `Slice`
- `Replay run`
- `Condition`
- `Packet`

They are the replay-native parts of the lab.

---

## What This Means

If we align to outside standards later:

- `Environment` is the umbrella idea
- this repo's concrete environment object is still the bounded `slice`
  inside a replay workspace
- the replay lab is not generic task-eval infrastructure
- it is replay-native eval infrastructure

So the minimal correct language is:

- **Bundle** is the frozen source package
- **Slice** is the bounded evidence surface
- **Replay run** evolves state over slices
- **Rollout** scores a probe under a condition
- **Packet** is the shareable/composed view
