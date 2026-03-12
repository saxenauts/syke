# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-337%20passing-brightgreen.svg)](https://github.com/saxenauts/syke/actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Syke is a Cross Web Agentic Memory. It is a specialized agent designed to maintain a unified memory of you, constructed from across your digital footprint. We model memory as an open ended system that evolves across time, works with all your agent harnesses as a complementary memory system.

The gateway to Syke memory is a single document called MEMEX.md, which can be loosely described as a dynamic self evolving map that changes shape and form to best model your world through LLMs. It is an agent managed markdown that serves as both a human readable dashboard, as well as a routing table for Syke agent to manage and maintain memory better. 

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
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
syke setup
```
</details>

---

## How It Works

Every day you run sessions across Claude Code, Cursor, Codex, Roo Code, Cline, Hermes, OpenClaw, Omo — different tools, different contexts. Projects, research, ops, personal. Your decisions, preferences, and context scatter across all of them.

Syke defragments you. One synthesis agent watches every surface, maintains one living memory, and shares it back to every session on a need to know basis.

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

Internally: SQLite + markdown memories + sparse links + an LLM that reads and reasons. No embeddings. No heuristics. The agent is the retrieval engine.

## Why This Architecture

The synthesis agent writes what it needs, links what matters, lets the rest decay. No schema designed upfront.
Every person's ontology is different. Don't design the architecture. Design the conditions. The map appears. Validate it through intelligent evolution over time. This is the correct phrasing for a complex open ended problem that is human modeling. 

Syke earlier was [Persona](https://github.com/saxenauts/persona) in 2024–2025. Neo4j + HNSW, graph-vector hybrid RAG. It hit 81.3% on LongMemEval (vs Graphiti's 71.2%), 65.3% on PersonaMem (vs Mem0's 61.9%), 69.0% on BEAM. Real work done there but it was agreed that agentic context engineering, and self improvement is the theme with agents performing long horizon task so everything that was needed in a graph+vector hybrid could now be done with agent, primitives and bash in a much lighter smarter, cheaper and faster way. 

So we built a sparse graph layer on SQLite, with each node being a markdown like story. All reasoning and ranking, reranking heuristics gets superseded by letting the agent run over this space and develops its own structure that is naturally personalized to you. 

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
| Claude Code | Sessions, tools, projects, git branches |
| Codex | Sessions, prompts, model/tool usage metadata |
| ChatGPT | Conversations, topics, timestamps |
| GitHub | Repos, commits, issues, PRs, stars |
| Gmail | Subjects, body text (truncated), labels, sent patterns |

All ingestion is local-first. Claude Code and Codex read from local session files. GitHub uses the REST API. Gmail uses OAuth (you authorize once, tokens stay local). ChatGPT requires a ZIP export — no API access needed.

---

## CLI

```bash
syke ask "question"   # Ask anything about yourself
syke context          # Dump memex to stdout (instant, local read)
syke record "note"    # Push an observation into memory
syke status           # Daemon + pipeline status
syke sync             # Manual one-time sync
syke doctor           # Health check
syke setup            # Interactive setup
```

`ask` spawns an agent that navigates your memories, follows links, cross-references platforms, and returns a grounded answer. `context` returns the memex instantly — local file read, no API call.

<details>
<summary>Daemon commands</summary>

The daemon runs in the background via launchd (macOS) or systemd (Linux). Setup installs it automatically.

```bash
syke daemon start     # Start background sync
syke daemon stop      # Stop the daemon
syke daemon status    # Check if running, last sync time
syke daemon install   # Reinstall daemon service
syke daemon uninstall # Remove daemon service
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

**OpenAI-compatible** (via LiteLLM — included with Syke):
```bash
syke auth set azure --api-key KEY --endpoint URL --model MODEL
syke auth set azure-ai --api-key KEY --base-url URL --model MODEL
syke auth set openai --api-key KEY
syke auth set ollama --model llama3.2        # no API key needed
syke auth set vllm --base-url URL --model MODEL
syke auth set llama-cpp --base-url URL --model MODEL
```

Claude Code session auth (`claude login`) is auto-detected if available, but is not the default.

**Provider resolution**: CLI flag > `SYKE_PROVIDER` env var > `~/.syke/auth.json` active_provider > auto-detect.
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

**[Setup Guide](docs/SETUP.md)** — Platform configuration, OAuth for Gmail

---

MIT · [Utkarsh Saxena](https://github.com/saxenauts)
