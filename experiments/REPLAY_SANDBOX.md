# Replay Sandbox

Continual evaluation for Syke's memory pipeline. Freezes a real user dataset, replays events day-by-day through synthesis from empty state, observes how the memex and memories evolve over time.

This is not a unit test. It is not a benchmark against ground truth. It is a **temporal replay** — the only way to see whether the synthesis agent builds coherent memory from a stream of real human activity, or whether it overbuilds, forgets, snowballs, or drifts.

---

## What This Is

The replay sandbox takes a frozen copy of real user events (agent sessions, GitHub activity, emails — everything Syke ingests) and feeds them to the synthesis pipeline one day at a time, starting from an empty memex. After each day's batch, it runs synthesis, snapshots the memex, records metrics, and advances.

```
Frozen Dataset (117 days, 128K events)
  │
  ├─ Day 1 events → Empty DB → synthesize() → memex v001 → snapshot
  ├─ Day 2 events → DB + v001 → synthesize() → memex v002 → snapshot
  ├─ ...
  └─ Day N events → DB + v(N-1) → synthesize() → memex v(N) → snapshot → JSON report
```

The output: a timeline of memex versions, per-cycle cost, memory counts, pointer counts, and a JSON report. Each memex version is saved as `memex/v001.md`, `v002.md`, etc.

## Why This Exists

Standard memory benchmarks (LongMemEval, PersonaMem, BEAM) batch-load all sessions and ask a question at the end. They measure **retrieval accuracy on a static dataset**. They cannot tell you:

- Does the memex grow proportionally to evidence, or does it overbuild on sparse data?
- Does the synthesis agent process its own traces (snowball effect)?
- At what point does memory quality plateau, degrade, or diverge?
- Does removing a feature (pointers, self-observation) change the trajectory?

These are **temporal accumulation dynamics** — they only surface when you replay in chronological order and observe at each step. No existing benchmark does this on real user data.

## The Frozen Dataset

**Private. Never released.**

| Field | Value |
|-------|-------|
| Source DB | `experiments/data/frozen_saxenauts.db` |
| User ID (source) | `fresh_test` |
| Events | 128,904 |
| Date range | 2025-08-20 to 2026-03-17 |
| Days | 117 |
| Sources | codex, opencode, claude, hermes, github |

This is saxenauts' real activity — AI agent sessions across every platform Syke ingests. The dataset was created on March 17, 2026 as a WAL-checkpointed copy of the production database, stripped to a single user, and frozen.

To replay someone else's data: create a fresh DB copy with `syke` CLI, provide it as `--source-db`.

## How to Run

```bash
# Dry run — count days and events without calling any LLM
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir /tmp/replay_output \
  --user-id replay_v1 \
  --source-user-id fresh_test \
  --dry-run

# 5-day test run
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir /tmp/replay_output \
  --user-id replay_v1 \
  --source-user-id fresh_test \
  --max-days 5

# Full 117-day replay (~2 hours, ~$3.50 with gpt-5-mini)
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir /tmp/replay_full \
  --user-id replay_v1 \
  --source-user-id fresh_test

# Start from a specific date
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir /tmp/replay_feb \
  --user-id replay_v1 \
  --source-user-id fresh_test \
  --start-day 2026-02-01
```

### Ablation Conditions

```bash
# Production synthesis (default — uses the real synthesis.md skill)
--condition production

# No pointers (strips pointer instruction from synthesis prompt)
--condition no_pointers

# Neutral (minimal prompt, no Syke-specific guidance)
--condition neutral
```

### Output Structure

```
/tmp/replay_output/
├── replay.db                    # The replay database (fresh, accumulates events)
├── replay_results.json          # Full timeline: per-day metrics, costs, memex stats
└── memex/
    ├── v001.md                  # Memex after day 1
    ├── v002.md                  # Memex after day 2
    └── ...
```

### Progress Output

```
Day 23/117 | 2026-02-18 | +3,450 events | memex: 4,231 chars | 7 pointers | $0.12
```

## What Gets Measured

Per cycle: day, events copied, memex chars, section count, pointer count (→ Memory: pattern), active memories, total memories, links, cost, turns, status.

Across conditions: the ablation comparison (`experiments/results/comparison_no_pointers_vs_neutral.md`) shows trajectory differences — pointer emergence requires explicit instruction, neutral prompts produce smaller but more stable memex, etc.

## Prior Runs

| Date | Condition | Days | Cost | Notes |
|------|-----------|------|------|-------|
| Feb 23 | production | 7 | ~$2 | First successful run |
| Feb 24 | production | 7 | ~$2 | Rerun for consistency |
| Feb 28 | no_pointers | 37 | $10.49 | Ablation: no pointer instructions |
| Feb 28 | neutral | 51 | $9.57 | Ablation: minimal prompt |
| Mar 5 | production | 3 batches | — | Batch experiment |
| Mar 16 | production | 3 | $0 | Failed — Kimi provider issues (fixed) |
| Mar 20 | production | 5 | $4.45 | Wrong source DB (used production, got 2014 GitHub dates) |

## Architecture Context

The replay sandbox exercises the full closed loop:

```
sense → observe → synthesize → distribute
  │                    │              │
  │              writes traces    reads memex
  │              (source='syke')
  │                    │
  └────────────────────┘
         self-observation
```

Key invariant (fixed March 20): synthesis excludes `source='syke'` events from its pending count and cursor. Traces stay in the DB for observability but do not pollute the synthesis input window. Without this filter, the agent processes its own exhaust — 6 user events generated 32 synthesis traces (84% noise) in the March 20 run.

## Research Context

What makes this different from standard eval harnesses:

**Temporal accumulation**: Standard benchmarks (LongMemEval, HELM, lm-evaluation-harness) evaluate against a static dataset. The replay sandbox evaluates how state evolves over a chronological stream. Closest academic term: **continual evaluation** (Mattdl et al., ICLR 2023 — "Continual Evaluation for Lifelong Learning").

**Real user data**: LongMemEval uses synthetic ShareGPT/UltraChat filler. MemoryStress generates synthetic sessions. The replay sandbox uses frozen production data from real agent sessions — ecological validity that synthetic benchmarks cannot provide.

**Self-observation loop**: DSPy's GEPA observes execution traces to optimize prompts. Mastra's observational memory compresses old conversations. Neither has the synthesis agent reading its own prior synthesis outputs as first-class timeline events. The closed self-observation loop is architecturally novel.

### Related Work

| System | Temporal | Real Data | Self-Observation | Accumulation Dynamics |
|--------|:--------:|:---------:|:----------------:|:---------------------:|
| LongMemEval | ✗ batch | ✗ synthetic | ✗ | ✗ |
| AgentMemoryBench | ✓ online mode | ✗ synthetic | ✗ | ✓ forgetting rate |
| MemoryStress | ✓ 4 phases | ✗ synthetic | ✗ | ✓ degradation slope |
| ContinualEvaluation | ✓ per-iteration | ✗ CL benchmarks | ✗ | ✓ stability gap |
| DSPy GEPA | ✗ unordered | ✗ task sets | ✓ trace reflection | ✗ |
| **Syke Replay Sandbox** | ✓ day-by-day | ✓ frozen production | ✓ closed loop | ✓ memex evolution |

### Suggested Metrics (Not Yet Implemented)

From the literature, metrics worth adding:

- **Recall@Age** (MemoryStress) — accuracy by fact age. Was it learned on day 5 or day 100?
- **Forgetting Rate** (AgentMemoryBench) — does the agent lose early information as new data arrives?
- **Degradation Slope** (MemoryStress) — linear regression on accuracy over time. Negative = decaying.
- **Stability Gap** (ContinualEvaluation, ICLR 2023) — temporary accuracy collapse when learning new information.

## Eval Layer — Validated Design (Not Yet Built)

The replay sandbox is a recorder. The eval layer makes it a scorer. Design validated March 20, 2026 — minimalism pass applied.

### Invariants

1. Agent under test cannot see gold answers during a run
2. Agent under test cannot modify eval criteria during a run
3. Dataset is frozen and checksummed before any run starts

### Structure

Two new files. No new orchestrator — checkpoint logic goes into `memory_replay.py` via `--eval` flag.

```
experiments/eval/
  dataset.json    ← checkpoint questions + gold answers (frozen, sha256 at run start)
  grader.py       ← LLM judge, pure function, post-hoc only

experiments/memory_replay.py  ← gains --eval flag, writes hypotheses.jsonl
```

### Checkpoint Question Schema (5 fields)

```json
{
  "id": "cp_day30_q1",
  "day": 30,
  "question": "What project was the user working on in late August 2025?",
  "answer": "Codex sessions across multiple repos",
  "rubric": "CORRECT if response names the project and approximate timeframe"
}
```

Cut from earlier design: `earliest_answerable_day`, `question_type`, `evidence_days`, `scoring.method`, `scoring.allow_off_by_one_days`, `authored_by`. All were analytics or dispatch logic that can wait for v2.

### How It Works

1. `--eval experiments/eval/dataset.json` flag on replay
2. Run start: load dataset, compute sha256, log it
3. Day loop: after synthesis, check if any checkpoint triggers on this day → fire `ask(question)` → append `{id, day, question, hypothesis, timestamp}` to `hypotheses.jsonl`
4. Run end: print "Grade with: `python experiments/eval/grader.py hypotheses.jsonl`"
5. Grader: reads `hypotheses.jsonl` + `dataset.json`, scores each hypothesis against gold answer via LLM judge, writes `scores.jsonl`

### Why Post-Hoc Grading

Grading after the run preserves the read-only boundary cleanly. No scoring during replay means no temptation to feed scores back, no mixed concerns, and you can re-grade with different rubrics without re-running. The `hypotheses.jsonl` file is the boundary object — replay writes it, grader reads it, they never share process.

### What Needs Doing (in order)

1. Author 5-10 checkpoint questions against the frozen 117-day dataset
2. Create `experiments/eval/dataset.json`
3. Add `--eval` flag + checkpoint probe logic to `memory_replay.py`
4. Create `experiments/eval/grader.py`
5. Run a 5-day test with 1-2 early checkpoints to validate the wiring

### What Waits for v2

Question types, evidence_days, earliest_answerable_day (analytics). Recall@Age, Forgetting Rate, Degradation Slope (metrics pipeline). Visualization of scores over time. Automated question authoring.

## Future Direction

Captured as intent, not committed:

1. **Visualization** — see memex evolution across cycles. Read-only view of how memories form, connect, and change. No editing controls.
2. **Proper module** — `experiments/memory_replay.py` may move to `syke/replay/` as a first-class module if it proves useful beyond experimentation.
3. **Agent self-improvement** — the replay sandbox could become a tool the memory agent uses to evaluate itself. Not implemented, not planned, but architecturally possible because the self-observation loop is closed.
4. **Metrics pipeline** — Recall@Age, Forgetting Rate, checkpoint questions at designated days.

---

*This experiment is private. The frozen dataset contains real user data and is never committed to git or released publicly. The documentation is neutral — anyone can replay their own data through the same pipeline.*
