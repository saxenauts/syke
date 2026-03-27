# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-553%20passing-brightgreen.svg)](https://github.com/saxenauts/syke/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Syke is agentic memory for people who use many AI tools and harnesses. It observes activity across those surfaces, synthesizes it into a memex, and feeds that memex back into future sessions as shared context.

The center of Syke is the memex: one mutable, agent-managed artifact that is both human-readable and agent-readable. The memex is not a report. It is a routing layer that evolves as the system learns what matters, what changed, and where deeper evidence lives.

## Quick Start

```bash
pipx install syke
syke setup
```

Setup detects your data sources, picks your LLM provider, ingests everything, starts the daemon. 


<details>
<summary>Other install methods</summary>

**uv tool install:**
```bash
uv tool install syke
syke setup
```

**From source:**
```bash
git clone https://github.com/saxenauts/syke.git && cd syke
uv sync --extra dev --locked
uv run syke setup
```
</details>

---

## How It Works

Every day you run sessions across Claude Code, Cursor, Codex, Roo Code, Cline, Hermes, OpenClaw, Omo — different tools, different contexts. Projects, research, ops, personal. Your decisions, preferences, and context scatter across all of them.

Syke binds together memory that would otherwise stay fragmented across tools. One synthesis loop watches many surfaces, maintains one memex, and shares it back into future sessions.

```
                        your agents, your contexts

     projects          research        ops             personal
  ┌───────────┐    ┌───────────┐  ┌───────────┐    ┌───────────┐
  │Claude Code│    │  Hermes   │  │ OpenCode  │    │    Omo    │
  │  Cursor   │    │ OpenClaw  │  │   Codex   │    │  ChatGPT  │
  │ Roo Code  │    │   Cline   │  │           │    │           │
  └─────┬─────┘    └─────┬─────┘  └─────┬─────┘    └─────┬─────┘
        │                │              │                 │
        │     sessions, subagents, context — scattered    │
        │                │              │                 │
        └────────────┐   │    ┌─────────┘   ┌─────────────┘
                     ▼   ▼    ▼             ▼
               ┌──────────────────────────┐
               │           Syke           │
               │                          │
               │   one synthesis agent    │
               │   one living memory      │
               │   updates every 15 min   │
               └────────────┬─────────────┘
                            │
                     unified context
                     shared back to
                     every session
                            │
        ┌───────────┬───────┴───────┬───────────┐
        ▼           ▼               ▼           ▼
   Claude Code   Hermes         OpenCode      Omo
     Cursor     OpenClaw          Codex      ChatGPT
    Roo Code     Cline                         ...
```

Internally: immutable observed timeline in SQLite, memex synthesis, and distribution back into agent environments. No embeddings. The agent is the retrieval engine.

## Why This Architecture

The current 0.5 development branch is centered on a simpler claim: a durable memex router can do useful work before a larger memory architecture is finalized. The observed timeline is immutable. The memex is mutable. Experiments decide how synthesis should evolve from there.

Syke earlier was [Persona](https://github.com/saxenauts/persona) in 2024–2025. Neo4j + HNSW, graph-vector hybrid RAG. It hit 81.3% on LongMemEval (vs Graphiti's 71.2%), 65.3% on PersonaMem (vs Mem0's 61.9%), 69.0% on BEAM. Real work done there but it was agreed that agentic context engineering, and self improvement is the theme with agents performing long horizon task so everything that was needed in a graph+vector hybrid could now be done with agent, primitives and bash in a much lighter smarter, cheaper and faster way. 

So we built Syke around an append-only observed timeline plus a single evolving memex. The agent reasons over evidence and rewrites the memex as needed. Additional memory structures remain an active design and eval question in the 0.5 branch.

Vector Embeddings are still useful, for multimodal data, but not for text, not where representing a human's memory is concerned.

Full story: **[Memex Evolution →](docs/MEMEX_EVOLUTION.md)**

Architecture details → [ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## The Numbers

One controlled benchmark: "What did I ship today?" Same model, same codebase, same minute. Manual multi-agent orchestration (omo default) vs `syke ask`. Feb 26 2026.

| Metric | Reduction |
|--------|-----------|
| Token usage | 55% fewer tokens (970K → 431K) |
| User-facing calls | 96% fewer calls (51 → 2) |
| Agents spawned | 3 → 0 (2 of 3 cancelled mid-flight) |

Syke isn't a replacement for your orchestrator — your orchestrator still runs. Memory has a freshness gap: up to 15 minutes. The reduction comes from pre-synthesized context, not from being faster.

---

## Platforms

| Platform | What's Captured |
|----------|-----------------|
| Claude Code | Sessions, tools, projects, branches |
| Codex | Sessions, prompts, tool/model metadata |
| ChatGPT | Exported conversations |
| Hermes | Distribution and harness events |
| OpenCode | Sessions and model metadata |

All ingestion is local-first. Claude Code, Codex, Hermes, and OpenCode read from local session files and databases. ChatGPT requires a ZIP export — no API access needed.

---

## CLI

```bash
syke ask "question"   # Ask anything about yourself
syke context          # Dump memex to stdout (instant, local read)
syke record "note"    # Push an observation into memory
syke status           # Ingestion + memex status
syke sync             # Manual one-time sync
syke doctor           # Health check
syke setup            # Interactive setup
```

`ask` spawns an agent that reads the current memex, crawls the observed timeline, and returns a grounded answer. `context` returns the current memex instantly — local read, no API call.

In restricted agent sandboxes, `syke ask` may not be able to open the live Syke store directly. The current fallback is to treat the memex and `syke context` as the guaranteed distribution surface, and run deeper `ask` queries from a trusted host shell when needed.

<details>
<summary>Daemon commands</summary>

The current daemon workflow is macOS-first. Setup installs a background loop on macOS. Broader backend support is still in progress on the 0.5 branch.

```bash
syke daemon start     # Start background sync
syke daemon stop      # Stop the daemon
syke daemon status    # Check if running, last sync time
syke daemon logs      # View daemon log output
```
</details>

---

## Privacy

One SQLite file: `~/.syke/data/{user}/syke.db`. 

A content filter strips API keys, OAuth tokens, credential patterns, and private message bodies before events enter the database. The daemon makes no network calls except to your configured LLM provider during synthesis. No telemetry. No analytics. No phone home.

There are plans here for actual cryptographic cross-device security, but current focus is on getting user modeling and federated memory right. 

---

## Auth

Syke works with any LLM provider. Setup shows a picker — choose whichever you have:

```bash
syke auth use codex                           # ChatGPT account (reads ~/.codex/auth.json)
syke auth set openrouter --api-key YOUR_KEY   # OpenRouter
syke auth set openai --api-key YOUR_KEY       # OpenAI direct
```

<details>
<summary>All supported providers</summary>

**Direct API key:**
```bash
syke auth use codex             # ChatGPT account via Codex
syke auth set openrouter --api-key KEY
syke auth set zai --api-key KEY
syke auth set kimi --api-key KEY
```

**Pi runtime providers:**
```bash
syke auth set azure --api-key KEY --endpoint URL --model MODEL
syke auth set openai --api-key KEY
syke auth set ollama --model llama3.2        # no API key needed
syke auth set vllm --base-url URL --model MODEL
syke auth set llama-cpp --base-url URL --model MODEL
```

`azure-ai` is still present in config/auth surfaces, but it is not mapped into the Pi runtime yet.

**Provider resolution**: CLI flag > `SYKE_PROVIDER` env var > `~/.syke/auth.json` active_provider.
</details>

---

## Configuration

Optional TOML at `~/.syke/config.toml`. All settings have sane defaults — this file is only needed when overriding behavior.

```bash
syke config show      # Print effective config
syke config init      # Generate config with defaults
```

See **[Config Reference](docs/CONFIG_REFERENCE.md)** for the full catalog.

---

## Agentic Context Engineering References

Syke is informed by a convergence in the research: memory should be agent-managed, not human-designed. 

**[RLM](https://arxiv.org/abs/2512.24601)** (Zhang, Kraska, Khattab — MIT, 2025) — The agent treats memory as an environment it navigates programmatically. No retrieval pipeline. The model is the retrieval engine.

**[ALMA](https://arxiv.org/abs/2602.07755)** (Xiong, Hu, Clune — 2026) — A Meta Agent searched over memory designs as executable code and beat every hand-crafted baseline by 6–12 points. Hand-designed memory is a ceiling.

**[ACE](https://arxiv.org/abs/2510.04618)** (Zhang et al. — Stanford/Salesforce, ICLR 2026) — Memory as an evolving playbook, not a static index. Contexts that accumulate strategies through generation, reflection, curation.

**[DSPy](https://github.com/stanfordnlp/dspy)** (Khattab et al. — Stanford) — Declarative programming for language models. Stop writing prompts by hand. Define what you want, let the optimizer figure out how.

**[GEPA](https://arxiv.org/abs/2507.19457)** (Agrawal, Khattab et al. — 2025) — Language is a richer learning medium than scalar reward. Evolutionary search over prompts driven by LLM reflection on execution traces.

**[Honcho](https://honcho.dev)** — Individual alignment infrastructure. What's learned about a user in one application should transfer across the entire agent ecosystem. Framing the problem as user modeling across agent boundaries.

**[Mastra](https://mastra.ai)** — Observational Memory hit 94.87% on LongMemEval with no vector database and no per-turn dynamic retrieval. The highest score recorded at the time of writing.

Five papers, same thesis: the agent discovers its own memory architecture, navigates it programmatically, maintains it as an evolving knowledge base, programs itself declaratively, and optimizes through reflection on its own execution.

---

## Learn More

**[Memex Evolution](docs/MEMEX_EVOLUTION.md)** — How the memex self-evolves from status page to emergent routing table. Evidence from 111 versions, pointer invention, ablation experiments.

**[Architecture](docs/ARCHITECTURE.md)** — Four-layer memory system, synthesis loop, design decisions (why SQLite, why free-form text, why Agent SDK)

**[Runtime + Replay Guide](docs/RUNTIME_AND_REPLAY.md)** — Current backend routing, ask/sync/daemon runtime flow, and practical replay experiment workflow

**[Packaging + Install Strategy](docs/PACKAGING_AND_INSTALL.md)** — How Syke should ship as a self-contained product across DMG, tool installs, source installs, and headless/SSH installs

**[Setup Guide](docs/SETUP.md)** — Platform configuration and source setup
---

MIT · [Utkarsh Saxena](https://github.com/saxenauts)
