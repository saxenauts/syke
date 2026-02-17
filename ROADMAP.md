# Syke Roadmap

Post-hackathon audit and priorities. Updated 2026-02-16.

---

## 0. Self-Healing Adapters ⬅ most urgent

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

## 1. Self-Update & Version Drift

**Problem**: The daemon plist/cron is a static file written once during `setup`. It points to a specific binary path (pipx or system). When we push updates to PyPI, the daemon keeps running old code. The MCP server (via uvx) auto-updates, but the daemon doesn't. Users on 0.2.7 daemons miss 0.2.8 fixes (chmod, interval, etc.) until they manually re-run setup.

**Tasks**:
- [ ] `syke self-update` command — upgrades pipx/pip install, rewrites plist/cron, reloads daemon
- [ ] Version check in daemon sync loop — log a warning if installed version < PyPI latest (check once per day, not every cycle)
- [ ] Version field in plist/cron metadata — so `daemon-status` can show "running 0.2.7, latest is 0.2.8"
- [ ] Consider: daemon rewrites its own plist if it detects a version mismatch after pipx upgrade

---

## 2. Guided Agentic Setup

**Problem**: Setup currently auto-detects local sources and runs. But credential setup (API keys, OAuth tokens) is fragile — depends on shell environment, varies by provider. The agent that ran setup for us couldn't find the API key because non-interactive shells don't source `.zshrc`.

**Tasks**:
- [ ] Provider-agnostic credential flow: `syke login` command that prompts for API key, writes to `~/.syke/.env`, chmod 600
- [ ] Per-provider setup guides: `syke add gmail`, `syke add github --token`, etc.
- [ ] Interactive setup wizard when `--yes` is not passed — ask about each provider, test credentials before proceeding
- [ ] Agent-friendly setup: detect when running inside Claude Code / Cursor / other agent and adapt messaging (e.g., "ask the user to paste their API key")
- [ ] First-run detection: if `~/.syke/` doesn't exist, print a one-liner setup guide instead of cryptic errors

---

## 3. Cost Center & Budget Controls

**Problem**: Perception costs $1.11/run (Opus full rebuild) or $0.08 (Sonnet incremental). ask() costs ~$0.02/call. There's no budget cap, no cost dashboard, no way to see cumulative spend. A runaway daemon could rack up costs silently.

**Tasks**:
- [ ] Cost budget in config: `max_daily_cost_usd`, `max_monthly_cost_usd` — daemon skips perception if budget exceeded
- [ ] Cost dashboard: `syke costs` command showing daily/weekly/monthly breakdown by operation (sync, perception, ask)
- [ ] Cost tracking in manifest: expose cumulative costs via MCP `get_manifest()` — already partially there (`profile_costs`), needs per-operation granularity
- [ ] Cost alerts: daemon logs a warning when approaching budget threshold
- [ ] Model selection controls: config option to use Sonnet-only for incremental (current default) vs Opus for full rebuilds, with cost implications shown

---

## 4. Service & MCP Architecture

**Problem**: The MCP server is monolithic — 8 tools in one server. The daemon is a simple sync loop. As we add more capabilities (ask, push, strategy evolution), the boundaries between "data layer", "intelligence layer", and "distribution layer" need sharpening.

**Tasks**:
- [ ] Inner/outer MCP design: separate "core data" tools (query, search, push — zero cost, always available) from "intelligence" tools (ask, perceive — require API key, cost money)
- [ ] MCP server health endpoint: expose server version, uptime, last sync time, profile freshness
- [ ] Daemon as service: consider systemd user service on Linux (alongside cron), launchd on macOS — with proper lifecycle management (start, stop, restart, logs)
- [ ] MCP server versioning: include syke version in server metadata so clients can detect stale servers
- [ ] Connection pooling: reuse DB connections across MCP tool calls (partially done — `_get_db()` caches, but no cleanup)
- [ ] Daemon CLI subcommand group: refactor `daemon-start/stop/status` (hidden flat commands) → `syke daemon start/stop/status` Click group, visible in --help

---

## 5. Manifest & Status Improvements

**Problem**: `get_manifest()` returns raw stats but doesn't tell you actionable things: is the profile stale? Is the daemon running? Are there errors? What's the cost trend?

**Tasks**:
- [ ] Health score: composite metric (profile freshness + daemon status + recent error rate + data recency)
- [ ] Actionable recommendations: "Profile is 3 days old, run `syke sync --rebuild`" or "Daemon not running, run `syke daemon-start`"
- [ ] Error log summary: last N errors from daemon sync, with timestamps
- [ ] Data freshness per source: "claude-code: 2 hours ago, github: 1 day ago, chatgpt: static import"
- [ ] Expose in MCP: make all of the above available via `get_manifest()` so agents can self-diagnose

---

## 6. ALMA: Experiments to Core

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

## 7. Interactive Consent & Onboarding

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

## 8. Live Daemon View

**Problem**: The daemon runs silently in the background. The only way to see what it's doing is `tail -f ~/.syke/data/saxenauts/syke.log` — raw log lines, no structure, no summary. There's no way to see at a glance: is it running, when did it last sync, what did it find, how much has it cost today.

**Tasks**:
- [ ] `syke watch` command — live TUI dashboard showing: daemon status (running/stopped), last sync time + cost, events collected today, profile age, recent log tail, today's total cost
- [ ] Rich live display: use Rich's `Live` layout — top panel for status, bottom panel for scrolling log, updates every 5s
- [ ] `syke daemon-status` upgrade: current output is one line. Expand to show last sync result, next scheduled sync, today's cost, any errors
- [ ] Daemon activity stream via MCP: push sync events into timeline so `query_timeline(source='syke-daemon')` shows operational history
- [ ] Log rotation: current `syke.log` grows unbounded — rotate at 10MB, keep 3 files

---

## 9. Platform Adapters

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

## 10. Testing & CI

**Tasks**:
- [ ] Integration tests: end-to-end setup → ingest → perceive → distribute flow with mocked LLM
- [ ] CLI integration tests: Click test runner for all commands
- [ ] MCP server integration tests: full request/response cycle
- [ ] CI matrix: test on Python 3.12, 3.13, 3.14 + Linux + macOS
- [ ] Coverage reporting: track test coverage, gate PRs on regression
- [ ] Load testing: how does the MCP server perform with 10K+ events?

---

## Priority Order

| Priority | Area | Why |
|----------|------|-----|
| P0 | Self-healing adapters (#0) | Silent data gaps, 404 noise every sync |
| P0 | Cost controls (#3) | Runaway daemon is a real risk |
| P0 | `syke login` (#2) | Biggest friction point for new users |
| P1 | Interactive consent (#7) | Trust and transparency before wider rollout |
| P1 | Live daemon view (#8) | Visibility into what's running |
| P1 | Self-update (#1) | Version drift causes silent degradation |
| P1 | ALMA to core (#6) | Proven tech sitting unused, 67% cost reduction |
| P2 | Manifest improvements (#5) | Agents can self-check health |
| P2 | Service architecture (#4) | Foundation for everything else |
| P3 | New adapters (#9) | More data = better profiles |
| P3 | Testing & CI (#10) | Quality gate |
