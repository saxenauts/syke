# ALMA — Agentic Learning through Meta-Architecture

> The agent learns *how* to explore, not just *what* to output.

## The Insight

Standard perception runs start cold every time. The agent explores a user's digital footprint, builds a profile, and throws away everything it learned about *how* to explore effectively. Each run makes the same mistakes — dead-end queries, missed cross-platform connections, redundant searches.

ALMA flips this: **let the agent evolve its own exploration strategy through iterative runs.** Like a spider building a web that gets denser with each pass, each perception run leaves a trace, deterministic reflection labels what worked, and the next run starts with accumulated wisdom.

No hand-designed memory architecture. No prompt engineering for search strategy. The agent discovers what works *for this specific user's data*.

## How It Works

```
                    ┌─────────────────────────────────────┐
                    │         MetaLearningPerceiver        │
                    │                                      │
  ┌──────────┐      │   ┌──────────────────────────────┐   │
  │ Adaptive │─────▶│   │  Agent SDK Loop (7 tools)    │   │
  │ Prompts  │      │   │                              │   │
  │ (w/ strat│      │   │  6 standard perception tools  │   │
  │  context)│      │   │  + read_exploration_history   │   │
  └──────────┘      │   └──────────┬───────────────────┘   │
       ▲            │              │                        │
       │            │              ▼                        │
       │            │   ┌──────────────────────────────┐   │
       │            │   │  ExplorationTrace            │   │
       │            │   │  (tool calls, searches,      │   │
       │            │   │   cross-refs, timing)        │   │
       │            │   └──────────┬───────────────────┘   │
       │            └──────────────┼────────────────────────┘
       │                           │
       │                           ▼
       │            ┌──────────────────────────────┐
       │            │  Deterministic Reflection    │
       │            │  (zero LLM cost)             │
       │            │                              │
       │            │  - Label searches: useful    │
       │            │    if query terms appear     │
       │            │    in final profile          │
       │            │  - Extract cross-platform    │
       │            │    connections               │
       │            └──────────┬───────────────────┘
       │                       │
       │                       ▼
       │            ┌──────────────────────────────┐
       │            │  ExplorationArchive           │
       │            │  (persistent on disk)         │
       │            │                              │
       │            │  traces/ + strategies/        │
       │            └──────────┬───────────────────┘
       │                       │
       │                       ▼  (every 3 runs)
       │            ┌──────────────────────────────┐
       │            │  Strategy Evolution           │
       │            │  (deterministic aggregation)  │
       │            │                              │
       │            │  - Productive searches        │
       │            │  - Dead ends to avoid         │
       │            │  - Source priorities           │
       │            │  - Cross-platform topics      │
       │            │  - Recommended tool sequence   │
       │            └──────────┬───────────────────┘
                               │
                               └──── feeds back to Adaptive Prompts
```

### The 4-Part System

**1. The 7th Tool — `read_exploration_history`**

The agent gets a tool to query its own past. Five aspects: `strategy` (full evolved strategy), `productive_searches` (what worked), `dead_ends` (what to avoid), `cross_platform` (known connections), `recent_traces` (last 3-5 runs with scores and tool sequences). The agent decides when and how to consult its history.

**2. Deterministic Reflection — Zero LLM Cost**

After each run, `reflect_on_run()` labels every search as useful or wasted by checking if query terms appear in the final profile text. Cross-platform connections are extracted from cross-reference results. No LLM calls — pure string matching. This is the signal that feeds strategy evolution.

**3. Strategy Evolution — Every 3 Runs**

`evolve_strategy()` aggregates traces weighted by profile score:
- **Productive searches**: queries with >50% hit rate, ranked by score-weighted relevance
- **Dead ends**: queries that returned empty 2+ consecutive times
- **Source priorities**: platforms that appear in high-scoring traces
- **Cross-platform topics**: connections found across traces, normalized by strength
- **Recommended tool sequence**: from the highest-scoring trace

All deterministic. All zero LLM cost.

**4. Adaptive Prompts — Strategy-Injected**

The next run's system prompt includes the accumulated strategy as context. The agent is told what worked, what to avoid, what connections to deepen. The prompt uses a "spider web" metaphor — early runs cast wide, later runs densify.

## The LLM Judge

Traditional eval counts words and checks fields. ALMA uses **Haiku as an LLM judge** — rating profiles on 4 criteria (1-10 scale):

| Criterion | What It Measures |
|-----------|-----------------|
| **Insight** | Does it reveal who this person *really* is — drives, tensions, identity? |
| **Actionability** | Could an AI assistant use this immediately to personalize responses? |
| **Specificity** | Real names, dates, projects, platforms vs generic platitudes? |
| **Coherence** | Unified portrait or disconnected fragments? |

Cost: ~$0.002 per eval call. The judge score (max weight 2.0x) combines with 6 deterministic dimensions (thread quality, identity anchor, voice patterns, source coverage, completeness, recent detail) for a composite score.

## Results

12 meta-learning runs, 4 strategy evolutions:

| Run | Strategy | Score | Cost | Key Insight |
|-----|----------|-------|------|-------------|
| 1-3 | v1: Concept Search | 82-87% | $0.45-0.60 | Broad exploration, finding the landscape |
| 4-6 | v2: Topic Expansion | 88-91% | $0.50-0.55 | Deepening productive searches, pruning dead ends |
| 7-9 | v3: Entity Discovery | 90-93% | $0.48-0.52 | Cross-platform connections emerge |
| 10-12 | v4: Refined Ranking | 92-94.3% | $0.42-0.48 | Efficient, focused, strategy-guided exploration |

**Peak: 94.3% at Run 5 — $0.60** (vs $1.80 baseline single-shot). The agent got better AND cheaper.

## File Guide

| File | Lines | What It Does |
|------|-------|-------------|
| `meta_perceiver.py` | ~550 | The ALMA engine. Builds the 7th tool, runs Agent SDK loop with adaptive prompts, captures traces, triggers reflection and strategy evolution. Entry point: `MetaLearningPerceiver.perceive()` or `.run_cycle()` |
| `reflection.py` | ~180 | Zero-cost deterministic reflection. `reflect_on_run()` labels searches, `evolve_strategy()` aggregates traces into strategies |
| `exploration_archive.py` | ~340 | Persistent traces + strategies on disk. ALMA-style softmax sampling biased toward high-scoring recent traces |
| `meta_prompts.py` | ~120 | Adaptive system/task prompts with strategy context injection |
| `eval.py` | ~610 | Evaluation framework: 6 deterministic dimensions + LLM judge (Haiku). Also includes freeform schema-agnostic evaluation |
| `meta_runner.py` | ~200 | Recording harness for live meta-learning runs. Dumps per-run artifacts (profile, trace, eval, strategy) |
| `test_eval.py` | ~405 | Tests for all evaluation dimensions (structured + freeform) |

## Running It

```bash
# From the repo root (with .venv activated)
# Single perception run with meta-learning
python -c "
from syke.db import SykeDB
from experiments.perception.meta_perceiver import MetaLearningPerceiver

db = SykeDB('utkarsh')
mp = MetaLearningPerceiver(db, 'utkarsh')
profile = mp.perceive(on_discovery=lambda kind, msg: print(f'[{kind}] {msg[:100]}'))
"

# Full 12-run meta-learning cycle
python -c "
from syke.db import SykeDB
from experiments.perception.meta_perceiver import MetaLearningPerceiver

db = SykeDB('utkarsh')
mp = MetaLearningPerceiver(db, 'utkarsh')
results = mp.run_cycle(n_runs=12, max_budget_usd=15.0)
"

# Run eval tests
python -m pytest experiments/perception/test_eval.py -v
```

## Key Design Decisions

- **Deterministic reflection over LLM-based reflection**: Reflection labels searches by string matching against the profile. Zero cost, reproducible, fast. An LLM could do deeper analysis, but the signal-to-cost ratio of string matching is hard to beat.
- **Strategy evolution every 3 runs, not every run**: Prevents overfitting to a single run's noise. 3 runs gives enough signal to distinguish real patterns from accidents.
- **The agent chooses when to consult history**: `read_exploration_history` is a tool, not forced context. The agent learns when its history is useful and when to explore fresh.
- **ALMA-style softmax sampling**: When the archive gets large, traces are sampled with bias toward high-scoring and recent entries — the same principle as ALMA's archive sampling in the original paper.
- **Haiku as judge, not self-evaluation**: The perceiver agent (Sonnet/Opus) doesn't rate itself. A separate, cheaper model (Haiku) provides the quality signal. This avoids the agent gaming its own metrics.
