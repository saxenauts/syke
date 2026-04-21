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
syke setup
syke doctor
syke context
syke ask "What changed this week?"
```

`syke setup` reviews the current setup plan first, guides provider selection if needed, validates the provider with a live Pi probe, ingests detected sources, runs initial synthesis when needed, and can enable background sync as a separate choice.

<details>
<summary>Other install methods</summary>

**uv tool install**

```bash
uv tool install syke
syke setup
```

**From source**

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
uv sync --extra dev --locked
uv run syke setup
```
</details>

### Use Syke through an agent

If you're an agent setting up Syke for a user, follow this sequence:

```bash
# 1. Install
pipx install syke          # or: uv tool install syke

# 2. Run agent setup
syke setup --agent         # returns JSON — read the "status" field

# 3. If status is "needs_runtime", install Node.js 18+ and retry setup
syke setup --agent

# 4. If status is "needs_provider", configure auth and retry
syke auth set <provider> --api-key <KEY> --use
syke setup --agent

# 5. Confirm health
syke doctor
```

Common providers: `anthropic`, `openai`, `azure-openai-responses`, `kimi-coding`, `openrouter`.
For Azure, also pass `--base-url https://<resource>.openai.azure.com/openai/v1 --model <model>`.

Do NOT run `syke setup` without `--agent` — that launches an interactive menu.

Once setup is done:

- `syke ask "..."` — deep recall across all sessions
- `syke record "..."` — save notes, decisions, TODOs
- `syke context` — read the current memex (fastest)
- `syke status` — check what's connected and running

After onboarding, Syke installs a skill file into detected agent harnesses and keeps a live memex updated every ~15 minutes.

## Why this loop is trustworthy

Syke separates capture from inference. Supported local harnesses feed raw activity into rollout traces stored in a single local database (`syke.db`). When Syke synthesizes memory or answers a question, it does so inside a flat workspace (`~/.syke/`) where trace history is read-only input, learned memory is writable, and the current memex is routed back out as additive context.

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
        | ~/.syke/                                  |
        |                                           |
        |  syke.db    rollout traces + learned mem  |
        |  PSYCHE.md  agent identity                |
        |  MEMEX.md   diary/map (4K token budget)   |
        |  adapters/  {source}.md per harness       |
        |                                           |
        |  daemon: synthesis on 15-min heartbeat    |
        |  ask and synthesis run here               |
        +-------------------------------------------+
                            |
             +--------------+---------------+
             |                              |
             v                              v
      direct reads                    routed context
      syke context                    syke ask
                                      MEMEX.md
                                      capability surfaces / SKILL.md
```

- `syke.db` stores rollout traces and learned memory in a single database.
- `PSYCHE.md` is the agent identity injected into every prompt.
- `MEMEX.md` is the current diary and map returned to future work.
- Adapters at `~/.syke/adapters/{source}.md` define how each harness is observed.

Current output-side scope is:

- export the canonical `MEMEX.md`
- inject PSYCHE + MEMEX + skill into every prompt (agent does not read files for basic context)
- temporal context injection (local time + UTC offset + cycle number)
- register the Syke capability package on supported harness capability surfaces

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

When a supported harness exposes a native capability surface, Syke can register its canonical Syke capability package there as part of distribution.

Adapters are markdown seeds shipped in `syke/observe/seeds/` -- there is no LLM-generated adapter factory. `ensure_adapters` runs during runtime initialization and copies the shipped seed for each detected harness into `~/.syke/adapters/{source}.md`. No Python adapter code is generated at runtime.

## Privacy and ownership

Everything lives in a flat workspace at `~/.syke/`. There is no per-user nesting or symlink indirection.

- `syke.db` is the single database (rollout traces and learned memory).
- `PSYCHE.md` is the agent identity.
- `MEMEX.md` is the current memex returned to future sessions.
- `adapters/{source}.md` define per-harness observation.
- A content filter strips API keys, OAuth tokens, credential patterns, and private message bodies before ingest.
- Network calls go only to your configured LLM provider during ask and synthesis.
- The OS sandbox enforces deny-default reads, catalog-scoped.

Users should have one place under their control for the scattered material their harnesses leave behind.

## What changes when Syke is running

The simplest change is that your agents stop starting from blank.

A decision made in one harness can show up in the next place where it matters. A useful pattern does not have to stay trapped inside one session. A question like "what did I ship today?" can be answered from accumulated work instead of being rebuilt from scratch.

The bigger bet is that memory management itself should improve from use. Syke keeps rollout traces alongside learned memory in a single store, then uses synthesis on a 15-minute heartbeat to keep reshaping the memex as a map. Over time, that lets the system learn better routes through a user's own history instead of forcing one fixed memory schema on everyone.

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
syke auth set openai --api-key YOUR_KEY --model gpt-5.4 --use
syke auth set openrouter --api-key YOUR_KEY --model openai/gpt-5.1-codex --use
syke auth login openai-codex --use
syke auth login anthropic --use
syke auth set zai --api-key KEY --model glm-5 --use
syke auth set kimi-coding --api-key KEY --model k2p5 --use
syke auth set azure-openai-responses --api-key KEY --endpoint URL --model gpt-5.4-mini --use
syke auth set localproxy --base-url URL --model MODEL --use
```
</details>

---

AGPL-3.0-only
