# Syke — Agentic Memory for AI

## What This Is
Syke is cross-web working memory for AI. It collects a user's digital footprint (Claude Code sessions, ChatGPT exports, GitHub activity, Gmail), uses the Claude Agent SDK with Opus 4.6 to synthesize a psyche-level understanding of who they are, and distributes that context to any AI tool via MCP server, CLAUDE.md injection, or JSON/markdown export.

Not a memory system — works WITH memory systems. Memory stores facts, Syke synthesizes psyche.

Built for Claude Code Hackathon (Feb 2026). MIT licensed.

## Agent Quick Start

If you're an AI agent setting this up for a user:

```bash
uvx syke setup --yes
```

Or with persistent install: `pipx install syke && syke setup --yes`

**If running as a Claude Code agent** (most common): The user is already authenticated
via `claude login` — perception and `ask()` work automatically. No extra step needed.

**If NOT a Claude Code session** (CI, standalone terminal), set API key first:

```bash
export ANTHROPIC_API_KEY=sk-ant-...  # costs ~$0.78/build, ~$0.02/query
uvx syke sync --rebuild
```

Or from source (for development/experiments):

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
cp .env.example .env  # Only needed for API-key path (not claude login)
python -m syke --user <name> setup --yes
```

The `setup` command auto-detects Claude Code sessions, ChatGPT exports in ~/Downloads, and GitHub via gh CLI. It collects everything, builds an identity profile (Agent SDK + MCP tools), and outputs CLAUDE.md + USER.md.

## Key Commands
```bash
source .venv/bin/activate

# Full pipeline (one command does everything)
python -m syke --user <id> setup --yes

# Keep profile up to date
python -m syke --user <id> sync               # Collect new data + incremental profile update
python -m syke --user <id> sync --force        # Override minimum event threshold
python -m syke --user <id> sync --rebuild      # Full ground-up profile rebuild
python -m syke --user <id> self-update         # Upgrade to latest PyPI release

# Check current state
python -m syke --user <id> status

# Tests
python -m pytest tests/ -v
```

Advanced commands (perceive, ingest, profile, serve, etc.) are available but hidden from `--help`. Run `syke <command> --help` directly if needed.

## Architecture
- **Data Collection**: Platform adapters in `syke/ingestion/` — each reads a data source and produces Event objects stored in SQLite
- **Perception** (unified in `agentic_perceiver.py`):
  - **Full** (Opus): Ground-up profile builds, deep identity work. Coverage-gated via `PermissionResultDeny` hooks. Triggered by `setup` or `sync --rebuild`.
  - **Incremental** (Sonnet): Delta-only updates merged into existing profile. Cheaper (~$0.08 vs $0.78). Default for `sync`.
  - **Multi-agent** (hidden): 3 Sonnet sub-agents explore in parallel, Opus synthesizes
- **Sync**: `syke/sync.py` — reusable sync logic (collect + optional profile update), minimum 5 events before triggering update (override with `--force`)
- **Distribution**: `syke/distribution/` — MCP server (FastMCP), formatters (JSON, Markdown, CLAUDE.md, USER.md), file injection

## Tech Stack
- Python 3.12+ with venv (.venv)
- Anthropic SDK (Opus 4.6 with extended thinking)
- Claude Agent SDK (agentic perception with custom MCP tools, coverage hooks, sub-agent delegation)
- Click CLI + Rich terminal output
- SQLite (WAL mode) for timeline storage
- Pydantic 2.x models
- FastMCP for MCP server

## Package Structure (~6,500 source lines, ~3,900 test lines)
```
syke/
├── cli.py                    # Click CLI (setup, sync, status + hidden commands)
├── sync.py                   # Sync business logic (collect + profile update)
├── config.py                 # .env, paths, API key, model defaults
├── db.py                     # SQLite schema + queries (WAL mode)
├── models.py                 # Pydantic: Event, UserProfile, etc.
├── metrics.py                # JSONL metrics, health checks, logging
├── ingestion/
│   ├── base.py               # BaseAdapter ABC + ContentFilter (privacy by design)
│   ├── claude_code.py        # Claude Code dual-store adapter (projects + transcripts)
│   ├── chatgpt.py            # ChatGPT ZIP export parser
│   ├── github_.py            # GitHub REST API + pagination
│   ├── gmail.py              # Gmail OAuth (gog CLI or Python OAuth)
│   └── gateway.py            # Unified ingestion gateway
├── perception/
│   ├── agentic_perceiver.py  # Agent SDK perception (single or multi-agent)
│   ├── tools.py              # 6 MCP tools + CoverageTracker
│   └── agent_prompts.py      # System/task prompts + sub-agent definitions
└── distribution/
    ├── formatters.py         # 4 output formats (JSON, MD, CLAUDE.md, USER.md)
    ├── inject.py             # File injection + MCP config
    ├── ask_agent.py          # Agentic ask() implementation
    └── mcp_server.py         # FastMCP server (8 tools, push + pull + ask + get_event)

experiments/                   # Experiment code (perception/ tracked, rest untracked)
├── cli_experiments.py         # Auto-registered experiment CLI commands (all hidden)
├── perception/                # ALMA meta-learning: strategy evolution, eval, reflection (7 files)
├── benchmarking/              # Benchmark runner, trace analysis, reports
├── simulation/                # 14-day federated push simulation
├── viz/                       # Interactive identity visualizer
├── daemon/                    # Background sync daemon + LaunchAgent (experimental)
└── stubs/                     # Platform adapter stubs (twitter, youtube)
```

## MCP Server Tools (8 tools)
- `ask(question)` — **Primary tool.** Agentic natural language queries about the user (uses Claude Code auth when available; falls back to API key — ~$0.02/call)
- `get_profile(format)` — Identity profile in json/markdown/claude-md/user-md
- `query_timeline(since, source, limit, summary)` — Events by date/source (summary=true strips content)
- `get_event(event_id)` — Full content for a single event by ID (zero cost)
- `search_events(query, limit, summary)` — Keyword search across events (summary=true strips content)
- `get_manifest()` — Data statistics and freshness
- `push_event(source, event_type, title, content, ...)` — Push events from any MCP client
- `push_events(events_json)` — Batch push

## Privacy Model
- Public sources (GitHub): no consent required
- Private sources (claude-code, chatgpt, gmail): require `--yes` flag or interactive consent
- Timeline data stays local in `~/.syke/data/{user}/syke.db`
- During perception, event data is sent to Anthropic API for analysis
- Pre-collection content filter strips credentials and private messaging content
- Strategy files contain search patterns only, no user content

## Release Process

**Branch workflow:** Work on branches, PR to main, CI runs tests. Never push directly to main.

**When to release:**
- New adapter or major feature → minor bump (0.x.0)
- Bug fixes, docs, cleanup → patch bump (0.x.x)
- No release needed for every merge — batch changes, release when ready

**How to release:**
```
/release <version> "<codename>"
```
Example: `/release 0.2.0 "The Agent Remembers"`

Claude runs tests, drafts changelog, you approve. tbump bumps version + commits + tags + pushes. GitHub Actions publishes to PyPI + creates GitHub Release.

**What ships on PyPI:** Only `syke/` package code. docs-site, viz, tests, examples stay in repo but aren't distributed.

**One-time setup (done):** PyPI trusted publishing configured for `saxenauts/syke` + `publish.yml`.
