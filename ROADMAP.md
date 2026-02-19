# Syke Roadmap

Post-hackathon audit and priorities. Updated 2026-02-18.

---

## Shipped

### 0.3.x — Claude Code Auth: Clean Slate

- ask() overrides stale ANTHROPIC_API_KEY via env_patch when ~/.claude/ is present
- setup no longer persists API key when Claude Code session auth is present
- daemon plist no longer bakes ANTHROPIC_API_KEY at install time
- MCP config (inject.py) no longer writes API key to claude_desktop_config.json

### API key gate removed (fixes #1) — ebee2fc

The redundant `ANTHROPIC_API_KEY` guard in `sync.py` has been removed. The Agent SDK auth chain resolves in order: `ANTHROPIC_API_KEY` env var → `~/.claude/` OAuth (via `claude login`). Claude Code Max/Team/Enterprise subscribers can run `syke sync` and the daemon without setting a separate API key. Users on other platforms (Codex, Kimi, Gemini CLI, etc.) still need `ANTHROPIC_API_KEY` — their platform auth is not usable by the Anthropic Agent SDK.

### Daemon logging — aac385b, c2aaeee, fcdbe11

Clean one-line stdout output with full ISO timestamps, no ANSI escape codes. New `syke daemon-logs` command for tailing logs. `syke daemon-status` now shows last-sync info (timestamp, events collected, profile update status).

### Self-Update & Version Drift — 0.3.0

- **`version_check.py`**: stdlib-only PyPI checker with 24h disk cache, zero new dependencies
- **`syke self-update`**: install-method-aware upgrade (pipx/pip/uvx/source each handled), stops/restarts daemon around the upgrade
- **Daemon drift detection**: `_sync_cycle` checks for updates each run, logs a WARN line, inserts a deduped timeline event per new version
- **`daemon-status` version display**: shows installed version and cached update-available notice (zero network cost)

---

## P0 — Multi-Platform Agent Executor ⬅ **NEW**

**Problem**: The Agent SDK is hardcoded to Anthropic/Claude. Users on Codex (OpenAI), Kimi (Moonshot), Gemini CLI, or any other AI platform cannot run perception without a separate Anthropic API key — even if they're already paying for AI via that platform. Syke should be self-installable from any major AI coding agent using that platform's credentials.

**Goal**: If the user is already on Codex, use OpenAI for perception. On Kimi, use Moonshot. No extra billing beyond their existing subscription.

**Architecture path**:
1. Abstract `AgentExecutor` interface — same MCP tools, swappable LLM backend
2. `OpenAIAgentExecutor` for OpenAI-API-compatible platforms (Codex, Kimi K2.5 via Moonshot's OpenAI-compatible endpoint, etc.)
3. `SYKE_MODEL` env var routes to the right executor (`anthropic`, `openai`, `kimi`)
4. Auto-detect from environment where possible (e.g., `OPENAI_API_KEY` set → use OpenAI executor)

The MCP perception tools are already model-agnostic. Only the agent loop needs to be swapped.

**Research spikes needed before implementation**:
- [ ] Confirm Agent SDK standalone auth behavior: when the daemon runs via launchd on a machine with no `ANTHROPIC_API_KEY` but with Claude Code login, does the Agent SDK pick up `~/.claude/` auth? Needs a real test, not just code reading.
- [ ] OpenAI Agents SDK + MCP: does it support MCP tools natively? (`HostedMCPTool` and `LocalShellTool` suggest yes — verify)
- [ ] Kimi OpenAI compatibility: is K2.5's API fully OpenAI-SDK-compatible for agentic loops? (surface-level research says yes, needs real test)
- [ ] Gemini CLI agent loop: can it host MCP servers? What does its execution model look like?
- [ ] Codex: uses OpenAI API — `OpenAIAgentExecutor` should cover it directly

**Tasks**:
- [ ] Define `AgentExecutor` abstract base class with same interface as current `AgenticPerceiver`
- [ ] Implement `OpenAIAgentExecutor` using OpenAI Agents SDK
- [ ] Add `SYKE_MODEL` / `SYKE_EXECUTOR` env var routing
- [ ] Auto-detect: if `OPENAI_API_KEY` set and no `ANTHROPIC_API_KEY`, default to OpenAI executor
- [ ] Update setup to detect which executor is available and set appropriate defaults
- [ ] Test Kimi via Moonshot API (OpenAI-compatible)
- [ ] Update docs with platform compatibility matrix

---

## 0. Architectural Refactor: CLI, MCP, Agent SDK Design ⬅ **FOUNDATIONAL**

**Problem**: Current architecture has implicit assumptions that create cost, complexity, and inflexibility issues:

1. **Nested agent cost problem**: When Claude Code (already running on Agent SDK) calls Syke's `ask()`, we spawn a NEW Agent SDK session (~$0.02/call). This double-charges for agent work that the parent agent could do itself. We already detect `CLAUDECODE` env var but remove it to avoid nesting — we should use it to enable intelligent fallback.

2. **Monolithic boundaries**: CLI commands, MCP server tools, and Agent SDK calls are tightly coupled. Cost controls, auth, and orchestration logic is scattered. No clear separation between "data layer" (free tools), "intelligence layer" (Agent SDK, costs money), and "distribution layer" (MCP server, file injection).

3. **Agent SDK execution context**: Both perception and ask() spawn independent Agent SDK sessions. No mechanism to reuse parent agent context or delegate work back to the calling agent when already in an agent environment.

**Intelligent Fallback Design**:

```
┌─────────────────────────────────────────────────────────┐
│ ask() or perceive() called                              │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │ Detect context       │
         │ (CLAUDECODE env var) │
         └──────────┬───────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
   ┌─────────┐           ┌──────────────┐
   │ PRESENT │           │ NOT PRESENT  │
   └────┬────┘           └───────┬──────┘
        │                        │
        ▼                        ▼
┌───────────────────┐    ┌──────────────────────┐
│ Return guidance   │    │ Spawn Agent SDK      │
│ to parent agent:  │    │ (current behavior)   │
│                   │    │                      │
│ "Use these MCP    │    │ Cost: $0.02–$1.11   │
│  tools directly:  │    └──────────────────────┘
│  - browse_timeline│
│  - search_footprint"
│                   │
│ Cost: $0.00       │
│ (parent pays)     │
└───────────────────┘
```

**Tasks** (organizes #1-11 below):

- [ ] **Agent context detection**: Don't remove `CLAUDECODE`, use it to detect parent agent and return tool guidance instead of spawning nested agent
- [ ] **Intelligent ask() fallback**: If called from Claude Code, return "use these MCP tools: browse_timeline(since='2024-01'), search_footprint(query='project')" instead of spawning $0.02 agent
- [ ] **Cost-aware API**: Expose both "spawn agent" and "return guidance" modes — let caller choose based on their context
- [ ] **Three-layer architecture**:
  - **Data layer** (free): MCP tools that read/write timeline (query, search, push, get_event)
  - **Intelligence layer** (paid): Agent SDK orchestration (ask, perceive) with smart fallback
  - **Distribution layer**: MCP server, CLAUDE.md, exports
- [ ] **CLI refactor**: Group commands by layer — `syke data query`, `syke intelligence ask`, `syke distribute serve`
- [ ] **MCP server split**: Separate data-only MCP server (always available) from intelligence MCP server (requires API key)
- [ ] **Reusable agent context**: Research if Agent SDK supports context reuse or delegation to avoid spawning redundant sessions

**Why this is foundational**: Sections #1-11 below all touch CLI, MCP server, or Agent SDK. Doing them piecemeal creates technical debt. This refactor establishes clean boundaries before adding features.

---

## P0 — Setup Output Clarity (bugs from Feb 18 live run)

Four misleading displays observed in the Feb 18 setup run. All cause users to misread successful runs as failures.

**Bug A — "0 conversations / 0 events" for deduplicated sources**

ChatGPT showed "0 conversations", GitHub showed "0 events" — both had all data already in DB from a prior run. These are 0 *new* events, not 0 total. Users read this as a failure.

Fix: show `OK  ChatGPT: 842 total (0 new)` instead of `OK  ChatGPT: 0 conversations`

**Bug B — "Building identity profile from 1 events"**

Perception step shows the number of new events ingested (1), not the total events in DB (3,942). The profile is built from all 3,942 events, but the display says 1.

Fix: pass total event count to the step display, not just the delta.

**Bug C — "1 events from 4 platforms" in final summary**

Same root cause as Bug B — final summary repeats the misleading delta instead of the total.

Fix: final summary should read "Built identity profile from 3,942 events across 4 platforms".

**Bug D — Cost display under session auth**

"$1.41" displayed even when session auth is active (billed to subscription, not API credits). Users think they're being charged from API balance when they're not.

Fix: annotate cost display — "$1.41 (via Claude subscription)" vs "$1.41 (API key)"

**Tasks**:
- [ ] Fix ingestion step output: show total + new counts, not just new
- [ ] Fix perception step: pass `total_events` (DB count) to display, not `new_events`
- [ ] Fix final summary: use DB total event count
- [ ] Detect session auth at display time and annotate cost accordingly

---

## 1. Self-Healing Adapters ⬅ most urgent

**Problem**: Adapters currently assume — default download paths, fixed export locations, no retry on partial failures. ChatGPT adapter assumes the ZIP is in `~/Downloads`. GitHub silently fails on every sync with a 404 for `Icarus_demo` README (and retries every 15 min instead of caching the miss). Claude Code adapter assumes the session store is at the default path. None of them recover gracefully when something isn't where expected.

**Consent problem**: `setup --yes` skips all interactive prompts and assumes everything. There's no flow to tell Syke "my ChatGPT export is at `/Volumes/external/chatgpt.zip`" or "skip Gmail entirely". Users with non-standard setups are silently not getting their data ingested.

**Tasks**:
- [ ] Cache 404s in DB — if a resource returns 404, mark it, skip retrying for 24h. Eliminates `Icarus_demo` noise in every sync.
- [ ] Path negotiation at setup: ask for export file locations instead of assuming `~/Downloads` — e.g., "Where is your ChatGPT export? (press enter to scan ~/Downloads)"
- [ ] Per-adapter skip flag: `syke setup --skip gmail` or interactive "skip this source?" during setup wizard
- [ ] Retry logic with backoff: transient HTTP errors (429, 5xx) retry with exponential backoff; permanent errors (404, 403) give up and log once
- [ ] Adapter health in manifest: per-source last success, last error, consecutive failure count
- [ ] Re-ingest from new path: `syke ingest chatgpt --file /path/to/export.zip` without full re-setup

---

## 2. Self-Update & Version Drift

**Problem**: The daemon plist/cron is a static file written once during `setup`. It points to a specific binary path (pipx or system). When we push updates to PyPI, the daemon keeps running old code. The MCP server (via uvx) auto-updates, but the daemon doesn't. Users on 0.2.7 daemons miss 0.2.8 fixes (chmod, interval, etc.) until they manually re-run setup.

Shipped in 0.3.0.

**Tasks**:
- [x] `syke self-update` command — upgrades pipx/pip install, rewrites plist/cron, reloads daemon
- [x] Version check in daemon sync loop — log a warning if installed version < PyPI latest (check once per day, not every cycle)
- [x] Version field in plist/cron metadata — so `daemon-status` can show "running 0.2.7, latest is 0.2.8"
- [ ] Consider: daemon rewrites its own plist if it detects a version mismatch after pipx upgrade

---

## 3. Guided Agentic Setup

**Problem**: Setup currently auto-detects local sources and runs. But credential setup (API keys, OAuth tokens) is fragile — depends on shell environment, varies by provider. The agent that ran setup for us couldn't find the API key because non-interactive shells don't source `.zshrc`.

**Tasks**:
- [ ] Provider-agnostic credential flow: `syke login` command that prompts for API key, writes to `~/.syke/.env`, chmod 600
- [ ] Per-provider setup guides: `syke add gmail`, `syke add github --token`, etc.
- [ ] Interactive setup wizard when `--yes` is not passed — ask about each provider, test credentials before proceeding
- [ ] Agent-friendly setup: detect when running inside Claude Code / Cursor / other agent and adapt messaging (e.g., "ask the user to paste their API key")
- [ ] First-run detection: if `~/.syke/` doesn't exist, print a one-liner setup guide instead of cryptic errors

---

## 4. Cost Center & Budget Controls

**Problem**: Perception costs $1.11/run (Opus full rebuild) or $0.08 (Sonnet incremental). ask() costs ~$0.02/call. There's no budget cap, no cost dashboard, no way to see cumulative spend. A runaway daemon could rack up costs silently.

**Note**: `max_budget_usd` already exists in `AgenticPerceiver` for per-run capping. Daily/monthly caps need tracking in `metrics.jsonl` + a check at sync time (read today's total before triggering profile update). This is straightforward to implement.

**Tasks**:
- [ ] Cost budget in config: `MAX_DAILY_COST_USD`, `MAX_MONTHLY_COST_USD` — daemon skips perception if budget exceeded (check `metrics.jsonl` totals before triggering)
- [ ] Cost per run visible in `syke daemon-status` — show last-run cost alongside last-sync timestamp
- [ ] Cost dashboard: `syke costs` command showing daily/weekly/monthly breakdown by operation (sync, perception, ask)
- [ ] Cost tracking in manifest: expose cumulative costs via MCP `get_manifest()` — already partially there (`profile_costs`), needs per-operation granularity
- [ ] Cost alerts: daemon logs a warning when approaching budget threshold
- [ ] Model selection controls: config option to use Sonnet-only for incremental (current default) vs Opus for full rebuilds, with cost implications shown

---

## 5. Service & MCP Architecture

**Problem**: The MCP server is monolithic — 8 tools in one server. The daemon is a simple sync loop. As we add more capabilities (ask, push, strategy evolution), the boundaries between "data layer", "intelligence layer", and "distribution layer" need sharpening.

**Tasks**:
- [ ] Inner/outer MCP design: separate "core data" tools (query, search, push — zero cost, always available) from "intelligence" tools (ask, perceive — require API key, cost money)
- [ ] MCP server health endpoint: expose server version, uptime, last sync time, profile freshness
- [ ] Daemon as service: consider systemd user service on Linux (alongside cron), launchd on macOS — with proper lifecycle management (start, stop, restart, logs)
- [ ] MCP server versioning: include syke version in server metadata so clients can detect stale servers
- [ ] Connection pooling: reuse DB connections across MCP tool calls (partially done — `_get_db()` caches, but no cleanup)
- [ ] Daemon CLI subcommand group: refactor `daemon-start/stop/status` (hidden flat commands) → `syke daemon start/stop/status` Click group, visible in --help

---

## 6. Manifest & Status Improvements

**Problem**: `get_manifest()` returns raw stats but doesn't tell you actionable things: is the profile stale? Is the daemon running? Are there errors? What's the cost trend?

**Tasks**:
- [ ] Health score: composite metric (profile freshness + daemon status + recent error rate + data recency)
- [ ] Actionable recommendations: "Profile is 3 days old, run `syke sync --rebuild`" or "Daemon not running, run `syke daemon-start`"
- [ ] Error log summary: last N errors from daemon sync, with timestamps
- [ ] Data freshness per source: "claude-code: 2 hours ago, github: 1 day ago, chatgpt: static import"
- [ ] Expose in MCP: make all of the above available via `get_manifest()` so agents can self-diagnose

---

## 7a. Perception Agent Reliability — Phantom Tool Calls

**Problem observed (Feb 18 run)**: The perception agent tried to call `Read` and `Grep` filesystem tools that are NOT in its `allowed_tools` list:

```
> Read file_path='/Users/saxenauts/.claude/projects/...' limit=300
> Grep pattern=title|content_preview path='...'
```

These silently failed. The agent spent 5–6 turns trying filesystem approaches before falling back to `search_footprint`. Wasted ~$0.20–$0.30 and inflated turn count from ~15 to ~22.

**Root cause**: The system prompt says "you have deep knowledge of a user's digital footprint" which primes the agent to try direct file access. The allowed tools list excludes filesystem tools, but the system prompt never says they're unavailable — the agent assumes it has them and burns turns finding out otherwise.

**Fix**: Add explicit note to `ASK_SYSTEM_PROMPT` in `agent_prompts.py`:

> "You do NOT have filesystem access. Use only the MCP tools listed below. Never attempt to call Read, Grep, Glob, or other file tools — they are not available in this context."

**Tasks**:
- [ ] Add filesystem exclusion notice to `ASK_SYSTEM_PROMPT` in `agent_prompts.py`
- [ ] Add same notice to perception system prompt in `agentic_perceiver.py`
- [ ] Consider: log a WARNING (not silent failure) when agent attempts a tool not in allowed_tools, so wasted turns are visible in metrics

---

## 7. ALMA: Experiments to Core

**Problem**: ALMA meta-learning code (strategy evolution, eval framework, reflection) lives in `experiments/perception/` (7 files, ~100KB). It's proven — 12 runs, peak 94.3% quality, 67% cheaper than baseline. But it's not integrated into the main perception pipeline.

**Files to migrate**:
- `experiments/perception/meta_perceiver.py` → strategy-aware perception
- `experiments/perception/eval.py` → automated quality scoring
- `experiments/perception/reflection.py` → post-run search analysis
- `experiments/perception/exploration_archive.py` → strategy storage
- `experiments/perception/meta_prompts.py` → strategy-injected prompts
- `experiments/perception/meta_runner.py` → orchestration

**Tasks**:
- [ ] Extract strategy storage from experiments — strategies should live in `~/.syke/data/{user}/strategies/`
- [ ] Integrate strategy reading into agentic perceiver — `read_previous_profile` tool already exists, add `read_strategy` tool
- [ ] Post-perception reflection: after each sync, run deterministic reflection (zero LLM cost) to label productive/wasted searches
- [ ] Strategy evolution on schedule: every N perception runs, evolve the strategy (promote good searches, cull dead ends)
- [ ] Eval as CI gate: run eval framework on perception output, log quality scores alongside cost in metrics
- [ ] Config flag: `strategy_evolution: true/false` — off by default, opt-in for users who want adaptive perception

---

## 8. Interactive Consent & Onboarding

**Problem**: `setup --yes` is great for agents but terrible for humans who want to understand what's being collected. There's no way to review what data will be touched before it's ingested. The current consent model is all-or-nothing — either skip everything with `--yes` or get a single prompt. No per-source granularity, no preview of what would be collected, no way to exclude sensitive sources.

**Tasks**:
- [ ] Per-source consent prompt: for each detected source, show what would be collected and ask yes/no before ingesting
- [ ] Data preview before ingest: "Found 842 ChatGPT conversations — show a sample? [y/N]"
- [ ] Consent record: write what was consented to and when in `~/.syke/consent.json` — auditable, re-checkable
- [ ] Revoke consent: `syke revoke gmail` — removes all Gmail events from DB and stops future ingestion
- [ ] `--interactive` flag as explicit opt-in to the full wizard (default for human installs, skipped by `--yes`)
- [ ] Privacy summary at end of setup: "Collected X events, filtered Y for private messages, Z credentials stripped"
- [ ] Human-oriented memory integration: research + prototype attachment to PKM tools (Obsidian, Notion, Logseq) — export profile as linked notes, sync identity graph to user's existing second brain

---

## 9. Live Daemon View

**Problem**: The daemon runs silently in the background. The only way to see what it's doing is `tail -f ~/.syke/data/saxenauts/syke.log` — raw log lines, no structure, no summary. There's no way to see at a glance: is it running, when did it last sync, what did it find, how much has it cost today.

**Tasks**:
- [ ] `syke watch` command — live TUI dashboard showing: daemon status (running/stopped), last sync time + cost, events collected today, profile age, recent log tail, today's total cost
- [ ] Rich live display: use Rich's `Live` layout — top panel for status, bottom panel for scrolling log, updates every 5s
- [ ] `syke daemon-status` upgrade: current output is one line. Expand to show last sync result, next scheduled sync, today's cost, any errors
- [ ] Daemon activity stream via MCP: push sync events into timeline so `query_timeline(source='syke-daemon')` shows operational history
- [ ] Log rotation: current `syke.log` grows unbounded — rotate at 10MB, keep 3 files

---

## 10. Distribution Channels

**Problem**: Syke currently distributes via MCP (Claude Code, Claude Desktop), CLAUDE.md injection, and JSON/markdown exports. But there are other AI coding tools with MCP support that should work out of the box.

**Tasks**:
- [ ] **Codex support** — Codex has a UI for connecting MCP servers with:
  - STDIO and Streamable HTTP transports
  - Bearer token authentication via env vars
  - Custom headers (static and from env)
  - Test the current MCP server with Codex, document setup
- [ ] Cursor MCP support — verify and document
- [ ] Windsurf MCP support — verify and document
- [ ] Zed MCP support — verify and document
- [ ] HTTP transport mode: current MCP server is STDIO-only, add optional HTTP mode for web-based tools
- [ ] Web dashboard: standalone web UI for viewing profile, asking questions, browsing timeline (alternative to MCP for non-Claude tools)

---

## 11. Platform Adapters

**Tasks**:
- [ ] Twitter/X adapter — archive export parsing (stub exists)
- [ ] YouTube adapter — watch history, liked videos (stub exists)
- [ ] Slack adapter — workspace export or API
- [ ] Notion adapter — API integration
- [ ] Linear/Jira adapter — issue tracking context
- [ ] Browser history adapter — Chrome/Firefox/Arc history for research context
- [ ] Calendar adapter — Google Calendar / iCal for schedule awareness
- [ ] Local markdown adapter: ingest local .md files (configurable paths, e.g. ~/Documents/personal/lifeOS/) — read, map directory structure, index by date/tag/topic

---

## 12. Testing & CI

**Tasks**:
- [ ] Integration tests: end-to-end setup → ingest → perceive → distribute flow with mocked LLM
- [ ] CLI integration tests: Click test runner for all commands
- [ ] MCP server integration tests: full request/response cycle
- [ ] CI matrix: test on Python 3.12, 3.13, 3.14 + Linux + macOS
- [ ] Coverage reporting: track test coverage, gate PRs on regression
- [ ] Load testing: how does the MCP server perform with 10K+ events?

---

## 13. Personal vs Commercial Auth — Anthropic Policy

**Signal**: Anthropic has been explicit that Claude Code session auth (`~/.claude/` OAuth) is for personal use. Routing API calls through a user's personal Claude subscription in a product or service violates ToS. API key path = commercial/business use.

**Current state**: Syke's `env_patch` in `ask_agent.py` prefers session auth when `~/.claude/` is present. This is correct for personal use. There is no guard for commercial deployments.

**Implications**:

1. **Current design is correct for personal use** — session auth working as intended, cost absorbed by subscription. This is the primary use case for Syke today.

2. **Multi-user / SaaS path requires API keys** — if Syke ever becomes a hosted service or is deployed for other users, it cannot use session auth. Must use `ANTHROPIC_API_KEY` with proper per-token billing.

3. **Documentation gap** — README and docs should be explicit: "Session auth is for personal use on your own machine. If you're building something for others, use an API key." Currently silent on this.

4. **The two-path architecture gets clearer**:
   - Personal: `~/.claude/` session auth, absorbed by subscription
   - Commercial: `ANTHROPIC_API_KEY`, metered per token

   The `env_patch` that prefers session auth is correct for personal use. For commercial deployments, `env_patch` should be disabled or overridden.

**Tasks**:
- [ ] Add ToS / personal-use notice to README and docs-site
- [ ] `SYKE_AUTH_MODE=personal|commercial` env var — `personal` uses session auth preference (current default), `commercial` requires API key and disables session auth fallback
- [ ] Guard in setup: if `ANTHROPIC_API_KEY` is set and `~/.claude/` exists, inform the user which auth path will be used for which operations

---

## Priority Order

| Priority | Area | Why |
|----------|------|-----|
| **P0** | **Setup output clarity (new)** | **Users misread successful runs as failures — active trust erosion** |
| **P0** | **Multi-platform executor (new)** | **Users on Codex/Kimi/Gemini can't use Syke today without extra Anthropic billing** |
| **P0** | **Architectural refactor (#0)** | **Foundation for all other work — eliminates nested agent costs, establishes clean boundaries** |
| P0 | Self-healing adapters (#1) | Silent data gaps, 404 noise every sync |
| P0 | Cost controls (#4) | Runaway daemon is a real risk; expand to include cost-per-run in `daemon-status` and `MAX_DAILY_COST_USD` caps |
| P0 | `syke login` (#3) | Biggest friction point for new users |
| P1 | Perception agent reliability (#7a) | Phantom tool calls waste money and inflate turn count |
| P1 | Personal vs commercial auth (#13) | Docs gap + ToS compliance before any multi-user work |
| P1 | Docs accuracy pass | README and docs-site need accurate auth model + honest multi-platform limitations stated |
| P1 | Interactive consent (#8) | Trust and transparency before wider rollout |
| P1 | Live daemon view (#9) | Visibility into what's running |
| P1 | Self-update (#2) | Version drift causes silent degradation |
| P1 | ALMA to core (#7) | Proven tech sitting unused, 67% cost reduction |
| P2 | Manifest improvements (#6) | Agents can self-check health |
| P2 | Service architecture (#5) | Subsumed by #0 refactor |
| P2 | Distribution channels (#10) | Reach more AI coding tools |
| P3 | New adapters (#11) | More data = better profiles |
| P3 | Testing & CI (#12) | Quality gate |
