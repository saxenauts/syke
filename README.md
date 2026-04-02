# Syke

[![PyPI](https://img.shields.io/pypi/v/syke)](https://pypi.org/project/syke/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
[![CI](https://github.com/saxenauts/syke/actions/workflows/ci.yml/badge.svg)](https://github.com/saxenauts/syke/actions/workflows/ci.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/license-AGPL--3.0--only-blue.svg)](LICENSE)

Syke is a local-first cross-harness agentic memory. It works with the AI harnesses you already use, keeps them in sync, and adapts its memory design to match your usage patterns.

AI-enabled work now means many sessions running in parallel across different harnesses, agents, repos, and chats. A decision made in one place often fails to reach the next place where it matters. Syke connects those threads so context carries forward.

That improves continuity and memory. It also makes the harnesses more efficient. They can rely more on what has already been discovered, decided, or learned, instead of starting over each time.

Syke introduces a kind of federation across your harnesses. Each harness can keep its own memory design and still rely on a general memory agent specialized in continuity across them. In that sense, Syke becomes a memory of memory.

## Start Here

### Use Syke directly

```bash
pipx install syke
syke auth set openai --api-key YOUR_KEY --model gpt-5-mini --use
syke setup
syke doctor
syke context
syke ask "What changed this week?"
```

`syke setup` reviews the current setup plan first. It ingests detected sources, runs initial synthesis when needed, and can enable background sync as a separate choice.

<details>
<summary>Other install methods</summary>

**uv tool install**

```bash
uv tool install syke
syke auth set openrouter --api-key YOUR_KEY --use
syke setup
```

**From source**

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
uv sync --extra dev --locked
uv run syke auth set openai --api-key YOUR_KEY --model gpt-5-mini --use
uv run syke setup
```
</details>

### Use Syke through an agent

Point the agent at this repo or the installed Syke skill, then let it drive the process.

If Syke is not set up yet, let the agent inspect the current plan with `syke setup --json` or `syke setup`, guide provider selection if needed, and finish setup first.

Once setup is done, a good agent workflow is:

1. call `syke ask` for deeper timeline and evidence-backed queries
2. call `syke context` when the current memex is enough
3. call `syke record` to write observations back into memory
4. call `syke status` for a quick operational snapshot
5. call `syke doctor` only when setup or runtime looks wrong

After sync and synthesis, Syke refreshes its local attachments and can install its skill file into detected skill-capable agent directories.

## Why this loop is trustworthy

Syke separates capture from inference. Supported local harnesses feed raw activity into an append-only events timeline. When Syke synthesizes memory or answers a question, it does so inside a local workspace where the events snapshot is read-only, the learned-memory store is writable, and the current memex is routed back out as additive context.

That gives you something simple to trust: a record you can inspect, a memory layer that carries forward, and a loop that stays local-first until ask or synthesis calls your configured provider.

## How It Works

```text
 Claude Code      Codex        Hermes       OpenCode
      \             |             |             /
       \            |             |            /
        +-----------+-------------+-----------+
                            |
                            v
           Observe: deterministic local ingest
                 no LLM before the ledger
                            |
                            v
        +-------------------------------------------+
        | events.db                                 |
        | immutable observed timeline               |
        | append-only evidence ledger               |
        +-------------------------------------------+
                            |
                            v
        +-------------------------------------------+
        | local Syke workspace                      |
        |                                           |
        |  read   events.db snapshot                |
        |  write  syke.db learned memory            |
        |  route  MEMEX.md                          |
        |                                           |
        |  ask and synthesis run here               |
        +-------------------------------------------+
                            |
             +--------------+---------------+
             |                              |
             v                              v
      direct reads                    routed context
      syke context                    syke ask
                                      MEMEX.md
                                      CLAUDE.md / AGENTS.md / SKILL.md
```

- `events.db` stores what happened.
- `syke.db` stores what Syke currently believes.
- `MEMEX.md` is the current map returned to future work.
- The raw timeline stays separate from learned memory.

## CLI

```bash
syke ask "question"   # deeper timeline and evidence-backed recall
syke context          # current memex, instant local read
syke record "note"    # write an observation back into memory
syke status           # quick operational snapshot
syke doctor           # deeper diagnostic
syke setup            # start or repair the system
syke sync             # manual refresh and synthesis cycle
```

Use `syke ask` when the agent needs more than the current memex. Use `syke context` when the current memex is enough and speed matters. Use `syke record` after useful work so the next session inherits it.

`syke status` is the quick snapshot. `syke doctor` is the deeper repair path.

<details>
<summary>Background sync commands</summary>

```bash
syke daemon start
syke daemon stop
syke daemon status
syke daemon logs
```
</details>

## Platforms

Syke discovers supported local harnesses from its built-in Observe catalog and their expected local paths. During setup, it scans those paths, checks what is actually present on disk, validates a shipped seed adapter when one exists, and ingests what it finds.

Supported local harnesses today:

- **Claude Code**: sessions, tools, projects, branches
- **Codex**: rollout sessions, thread metadata, tool and model metadata
- **OpenCode**: SQLite sessions and model metadata
- **Cursor**: local chat/session state from official user-data roots
- **GitHub Copilot**: Copilot CLI session state plus VS Code chat session files
- **Antigravity**: workflow artifacts, walkthroughs, and browser recording metadata
- **Hermes**: SQLite/session history and tool traces
- **Gemini CLI**: chat recordings and checkpoint artifacts

Current active discovery roots in code include:

- `~/.claude/projects`
- `~/.claude/transcripts`
- `~/.codex`
- `~/Library/Application Support/Cursor/User/...` or `~/.config/Cursor/User/...`
- `~/.copilot/session-state`
- `~/Library/Application Support/Code/User/...` or `~/.config/Code/User/...`
- `~/.gemini/antigravity`
- `~/.hermes`
- `~/.gemini/tmp`
- `~/.local/share/opencode`

All ingestion is local-first. Syke reads these surfaces from local files and local databases.

When a supported harness exposes a native skill directory, Syke can also install its `SKILL.md` there as part of distribution.

For supported harnesses, setup is now seed-first. It validates the shipped adapter for the detected source, deploys it into the user adapter directory when validation passes, and only falls back to the Observe factory when a shipped seed is missing or fails validation on the local artifact shape.

If your harness layout is unusual, or if you want to connect a new harness yourself, use:

```bash
syke connect /path/to/your/harness
```

The factory remains the repair and unknown-harness path. It inspects real local artifacts, writes one adapter, validates it strictly, and deploys it into Syke's local adapters directory.

## Privacy and ownership

Canonical user stores live under `~/.syke/data/{user}/`. The workspace mirrors current state locally for synthesis and ask flows.

- `events.db` is the immutable observed ledger.
- `syke.db` is the learned-memory store.
- `MEMEX.md` is the current memex returned to future sessions.
- A content filter strips API keys, OAuth tokens, credential patterns, and private message bodies before ingest.
- Network calls go only to your configured LLM provider during ask and synthesis.

Users should have one place under their control for the scattered material their harnesses leave behind.

## What changes when Syke is running

The simplest change is that your agents stop starting from blank.

A decision made in one harness can show up in the next place where it matters. A useful pattern does not have to stay trapped inside one session. A question like "what did I ship today?" can be answered from accumulated work instead of being rebuilt from scratch.

The bigger bet is that memory management itself should improve from use. Syke keeps the raw timeline separate from learned memory, then uses synthesis to keep reshaping the memex as a map. Over time, that lets the system learn better routes through a user's own history instead of forcing one fixed memory schema on everyone.

One controlled example: on February 26, 2026, the same question was asked against the same codebase in the same minute: "What did I ship today?" Manual multi-agent orchestration was compared with `syke ask`.

| Metric | Result |
|--------|--------|
| Token usage | 55% fewer tokens, from 970K to 431K |
| User-facing calls | 96% fewer calls, from 51 to 2 |
| Agents spawned | 3 to 0 |

This is one measured example from one workflow on one date. Freshness still has a gap of up to 15 minutes. The current claim is narrower and more useful: continuity can reduce reconstruction, and memory can get better at routing through repeated use.

## Learn More

**Start here**

- [Setup Guide](docs/SETUP.md)
- [Providers](docs/PROVIDERS.md)
- [Platforms](PLATFORMS.md)

**Runtime and internals**

- [Runtime Guide](docs/RUNTIME_AND_REPLAY.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Config Reference](docs/CONFIG_REFERENCE.md)

**Story and evolution**

- [Memex Evolution](docs/MEMEX_EVOLUTION.md)

<details>
<summary>Provider examples</summary>

```bash
syke auth set openai --api-key YOUR_KEY --model gpt-5-mini --use
syke auth set openrouter --api-key YOUR_KEY --use
syke auth use codex
syke auth set zai --api-key KEY --use
syke auth set kimi --api-key KEY --use
syke auth set azure --api-key KEY --endpoint URL --model MODEL --use
syke auth set ollama --model llama3.2 --use
syke auth set vllm --base-url URL --model MODEL --use
syke auth set llama-cpp --base-url URL --model MODEL --use
```
</details>

---

AGPL-3.0-only
