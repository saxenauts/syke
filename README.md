# Syke

[![PyPI](https://img.shields.io/pypi/v/syke.svg)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-346%20passing-brightgreen.svg)](https://github.com/saxenauts/syke)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Agentic memory for your AI tools. A daemon that watches your platforms — code, conversations, commits, emails — synthesizes them into a living model of who you are, and serves it to every AI tool you use via MCP.

## Quick Start

```bash
uvx syke setup --yes
```

That's it. Syke auto-detects your username, finds local data sources (Claude Code sessions, ChatGPT exports), configures MCP, and starts the daemon. Memory synthesis requires auth — run `claude login` (Claude Code subscription). Without auth, setup will fail.

<details>
<summary>Other install methods</summary>

**pipx** (persistent install):
```bash
pipx install syke
syke setup --yes
```

**pip** (in a venv):
```bash
pip install syke
syke setup --yes
```

**From source**:
```bash
git clone https://github.com/saxenauts/syke.git && cd syke
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python -m syke setup --yes
```
</details>

## Auth

Syke requires Claude Code authentication:
```bash
claude login
```
Works with Max, Team, or Enterprise plans.

## What It Does

Every AI session starts from zero. Your context is scattered — commits in GitHub, threads in ChatGPT, sessions in Claude Code, emails in Gmail. Each tool sees a slice. None see you.

Syke fixes this. A background daemon syncs your platforms every 15 minutes, an AI agent synthesizes what it finds into memories, and the result — a self-evolving map of who you are — is served to every MCP-compatible tool.

**The output — your memex:**

```markdown
# Memex — dev

## Identity
Full-stack engineer building AI developer tools. Python, TypeScript, React.

## What's Active
- **Syke v0.4.0** [high] (github, claude-code): Shipping storage rewrite,
  pre-release audit, README overhaul. 29 commits pushed today.
- **Client project** [medium] (gmail, github): API integration due Friday.

## Context
Deep in release mode. Communication style: direct, fast-paced, technical.
Prefers concise answers. Working late nights this week.

## Recent Context
Merged storage branch (85 files changed). Fixed auth bug in synthesis.
Running 346 tests. Preparing v0.4.0 tag.

---
Sources: claude-code, github, chatgpt, gmail. Events: 847.
```

This is what `get_live_context` returns. Every AI tool you connect reads this first — instant context, no "tell me about yourself."

## Three Verbs

Syke exposes 3 MCP tools. That's the entire public API.

| Tool | What it does | Cost |
|------|-------------|------|
| `get_live_context` | Returns the memex — your synthesized identity map. Often all an agent needs. | Free (local read) |
| `ask` | Explores the memory layer to answer any question about you. "What did I work on last week?" "How do I feel about MongoDB?" Note: ~50s timeout in MCP context. | ~$0.10-0.20/call |
| `record` | Pushes observations from the current session back into your timeline. | Free (local write) |

**`get_live_context`** is instant — it reads a local file. **`ask`** spawns an AI agent that navigates your memories, follows links, cross-references platforms, and returns a grounded answer. **`record`** lets any tool contribute context back, so your model grows from every session.

## How It Works

```
Your Platforms          Syke Daemon              Any MCP Client
─────────────          ───────────              ──────────────
Claude Code ──┐                                 ┌── Claude Code
ChatGPT ──────┤  collect   ┌──────────┐  serve  ├── Codex
GitHub ───────┼──────────► │  Memex   │ ◄───────┼── Cursor
Gmail ────────┤  every     │  (map)   │  via    ├── Kimi
MCP push ─────┘  15 min    └──────────┘  MCP    └── Custom agents
                     │           ▲
                     ▼           │
               ┌──────────┐     │
               │ Synthesis │─────┘
               │  Agent    │
               └──────────┘
              Reads events,
              writes memories,
              updates the map
```

**The loop**: Collect signals → synthesize memories → update the map → serve to all tools → collect new signals from those tools → re-synthesize. Every 15 minutes.

Internally, the synthesis agent navigates your memory with 15 tools — full read/write access to create, link, update, supersede, and retire knowledge. It decides what's worth remembering. No heuristics, no embeddings — just an LLM reading and writing text.

## Supported Platforms

| Platform | Method | What's Captured |
|----------|--------|-----------------|
| Claude Code | Local JSONL parsing | Sessions, tools, projects, git branches |
| ChatGPT | ZIP export parsing | Conversations, topics, timestamps |
| GitHub | REST API | Repos, commits, issues, PRs, stars |
| Gmail | OAuth API | Subjects, snippets, labels, sent patterns |

## Daemon Commands

```bash
syke ask "question"   # Ask anything about yourself (primary CLI command)
syke context          # Dump current memex to stdout
syke doctor           # Verify auth, daemon, DB health
syke mcp serve        # Start MCP server (for config injection)
syke daemon start     # Start background sync (every 15 min)
syke daemon stop      # Stop the daemon
syke daemon status    # Check if running, last sync time
syke sync             # Manual one-time sync
syke self-update      # Update to latest version
```

## Privacy

All data stays local in `~/.syke/data/{user}/syke.db` — one SQLite file per user, copy it anywhere. Nothing leaves your machine except during synthesis (Anthropic API calls, under their [data policy](https://www.anthropic.com/privacy)). A pre-collection content filter strips credentials and private messages before events enter the database.

## Learn More

**[Architecture](docs/ARCHITECTURE.md)** — Three-layer memory system, synthesis loop, design decisions (why SQLite over vector DB, why free-form text, why Agent SDK)


**[Memex Evolution](docs/MEMEX_EVOLUTION.md)** — 8-day replay showing how the memex graduates from status page to routing table

**[Setup Guide](docs/SETUP.md)** — Detailed installation, platform configuration, OAuth setup for Gmail

**[Docs Site](https://syke-docs.vercel.app)** — Full reference documentation

**[Demo](https://syke-ai.vercel.app)** — Live visualization of the synthesis process

**[Video walkthrough](https://youtu.be/56oDe8uPJB4)** — 5-minute overview of how Syke works

---

MIT · [Utkarsh Saxena](https://github.com/saxenauts)
