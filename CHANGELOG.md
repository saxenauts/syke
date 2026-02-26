# Changelog

All notable changes to Syke are documented here.


## [0.4.3] — 2026-02-26 — "The Voice"

Syke speaks. Streaming ask, behavioral skill rewrite, resilience hardening, docs decoupled from CLAUDE.md.

### Added
- **Streaming `syke ask`** — real-time output with thinking→stderr (dim italic), text→stdout, tool calls→stderr (dim), cost footer on stderr. AskEvent dataclass, `ask_stream()` entry point, StreamEvent delta handling. 16 new tests.
- **Ask timeout & early-output resilience** — `asyncio.wait_for` with 120s configurable timeout (`ASK_TIMEOUT`), early stdout byte before SDK init prevents premature process kill, SIGTERM handler dumps local fallback before exit. Fixes empty output bug where 3.6–7.5s thinking window produced zero stdout.
- **SKILL.md behavioral rewrite** — repositioned from identity-query tool to behavioral contract. Agents proactively read and write through natural trigger framing, not explicit checklists. Description catches implicit intent through positioning.
- **SVG architecture diagram** — light/dark GitHub theme support via `<picture>` element.

### Changed
- Docs decoupled from CLAUDE.md — README, SKILL.md, SETUP.md, MEMEX_EVOLUTION.md, context_files.py all use platform-agnostic "memex" wording. The memex is its own thing, not "the CLAUDE.md file."
- CONTRIBUTING.md: "CLAUDE.md injection" → "Memex distribution"
- `CancelledError` cleanup for graceful SDK shutdown
- Stale MCP server references removed from docs, tests, and hooks
- 393 tests passing (was 389).

### Fixed
- Empty `syke ask` output when process killed during SDK init window (closes #2)
- Streaming support for `syke ask` (closes #6)


## [0.4.2] — 2026-02-25 — "The Harness"

Cross-agent memory distribution. Syke now installs itself into other AI agents on your system.

### Added
- **Harness adapter system** (`syke/distribution/harness/`) — framework for installing Syke context into other AI agents. HarnessAdapter ABC with detect/install/status/uninstall interface, protocol-resilient design (adapters declare protocol + version).
- **Hermes adapter** — full A/B test mode: installs SKILL.md at `~/.hermes/skills/memory/syke/`, coexists with native MEMORY.md + USER.md without touching them.
- **Claude Desktop adapter** — adds Syke data dir to `localAgentModeTrustedFolders` in config JSON.
- **Pi adapter** — detection stub (checks `~/.pi`, `~/.config/pi`, `~/.config/piebald`).
- **`syke record`** CLI command — push observations into memory: plain text, piped stdin, `--json`, `--jsonl` batch, `--tag`, `--source`. Thin wrapper around IngestGateway.push(), no post-record synthesis.
- **`syke ask` local fallback** — queries memex + keyword-matched memories from SQLite when Agent SDK is unavailable. All error paths route to fallback.
- **Dashboard shows connected agents** — bare `syke` now displays `Agents: hermes, claude-desktop` line for detected platforms.
- **Doctor shows harness status** — `syke doctor` reports detected/connected/not-found for each adapter with notes.
- **GitHub issue #8** for community adapter requests — replaces inline TODOs.
- SKILL.md updated with `record` command docs and `license: MIT` per agentskills.io spec.

### Fixed
- Dashboard reads memex from DB and daemon from launchd (was checking stale file paths).
- Removed 4 dead imports: `user_data_dir` (cli.py), `Path` (synthesis.py), `bootstrap_memex_from_profile` (synthesis.py), `SykeDB` (gmail.py).
- Cleaned stale pycache files from removed modules.

### Changed
- Harness `install_all()` runs during `syke setup` Step 4 (auto-connects detected agents).
- Daemon synthesis refresh triggers harness re-install (keeps agent context fresh).
- Test counts updated across docs (346→361 in README, CONTRIBUTING, ARCHITECTURE).
- 389 tests passing (was 346).


## [0.4.1] — 2026-02-24

### Breaking
- Removed ANTHROPIC_API_KEY support entirely. Auth is now Agent SDK auth-only — Syke never manages API keys or tokens. Users must run `claude login` to authenticate.
- `syke setup` now requires auth (hard fail without it). No "data-only" mode.

### Added
- `syke ask "question"` promoted from hidden to primary CLI command
- `syke context` — dump current memex to stdout
- `syke doctor` — verify auth, daemon, DB health
- `syke mcp serve` — stdio MCP server command (replaces hidden `syke serve`)
- Bare `syke` (no subcommand) shows status dashboard
- MCP ask() tool now has bounded ~50s timeout (resolves timeout issues with Claude Desktop)

### Fixed
- daemon/metrics.py: Fixed crash from importing nonexistent GITHUB_TOKEN from config
- Removed env_patch mechanism that cleared API keys when session auth was available
- Removed internal Agent SDK parser monkey patch; ask() now uses public SDK APIs only

## [0.4.0] — 2026-02-24 — "The Map Remembers"

Storage rewrite. Profiles are gone — replaced by a three-layer memory system where an AI agent builds and maintains a living map of who you are.

- **Breaking**: UserProfile-based perception replaced by memex architecture. `get_live_context` now returns the memex (agent-written map), not a profile. Old profiles auto-bootstrap into memex on first sync.
- **Memory system**: Three layers — evidence ledger (immutable events), memories (agent-written knowledge), memex (navigational map). 15 tools (10 read, 5 write) give the synthesis agent full CRUD over the memory layer.
- **Synthesis rewrite**: Agent SDK loop replaces single-shot perception. Orient → Extract & Evolve → Update the Map. ~$0.25/cycle (Sonnet, 10 turns max).
- **Storage**: SQLite + FTS5 + WAL. BM25 full-text search over memories and events. Single file per user at `~/.syke/data/{user}/syke.db`.
- **MCP**: Public API unchanged (3 tools: `get_live_context`, `ask`, `record`). Internal tools expanded from 6 to 15.
- **Auth fix**: `env_patch` no longer force-clears `ANTHROPIC_API_KEY` when `~/.claude` exists — API-key-only setups work again.
- **Removed**: `syke perceive` command, `perception/` module, beautifulsoup4/lxml/browser-use/playwright dependencies.
- **Docs**: README overhauled (330→166 lines). Architecture detail moved to `docs/ARCHITECTURE.md`. Research references corrected with proper citations (RLM, ALMA, LCM). Memex evolution doc added (`docs/MEMEX_EVOLUTION.md`). Docs-site MCP tools page rewritten for 3-tool surface.
- **Experiments**: Synthesis replay format + generator script for day-by-day memory evolution traces.
- 346 tests passing (was 297).

## [0.3.5] — 2026-02-21 — "Three Verbs"

MCP surface reduced from 10 tools to 3 — Syke is a memory agent, not a database API.

 **Breaking**: MCP tool surface reduced to 3 verbs: `get_live_context` (read profile), `ask` (reason over timeline), `record` (push observations). Removed: `push_event`, `push_events`, `get_profile`, `query_timeline`, `get_manifest`, `search_events`, `get_event`.
 Refactor: `ask_agent.py` restored to single-function architecture (`_run_ask`), removed timeout wrapper and `_run_agent` split
 Removed: `ASK_TIMEOUT_S` config and all `asyncio.wait_for` timeout machinery — no silent truncation
 Config: `ASK_MAX_TURNS` raised to 8 (was 5)
 Docs: README, SKILL.md, and strategy files updated to reflect 3-tool surface
 Housekeeping: ROADMAP.md untracked from repo, .gitignore consolidated

## [0.3.4] — 2026-02-19 — "Rate Limit Resilience"

Patch ask() to survive two CLI 2.1.45 breaking changes: nested session protection and rate_limit_event advisory messages.

- Fix: clear `CLAUDECODE` env var before Agent SDK subprocess spawn — CLI 2.1.45 refuses nested sessions, making every MCP ask() silently return empty
- Fix: patch `parse_message` at module load to return `SystemMessage` for `rate_limit_event` instead of raising — stream continues to actual answer
- Fix: fallback message updated to "Try `syke sync`" instead of "Try rephrasing"
- Tests: add coverage for rate_limit_event before real response and CLAUDECODE env clearing

## [0.3.3] — 2026-02-18 — "Steady State"

ask() is now resilient to API throttling; agent config is env-overridable.

### Fixed
- ask() no longer crashes on unknown stream events (e.g. `rate_limit_event`) — catches `ClaudeSDKError`, logs a warning, and returns a partial answer instead of erroring out
- Upgrade `claude-agent-sdk` floor to 0.1.38
- Timeline display: readable timestamps, colors, no line-wrapping, clean titles

### Changed
- Agent config centralized in `syke/config.py` — model, budget, and turn settings are all env-overridable (`SYKE_ASK_MODEL`, `SYKE_ASK_BUDGET`, `SYKE_SYNC_MODEL`, `SYKE_REBUILD_MODEL`, etc.)
- ask() budget raised from $0.15 to $1.00 default (analysis of 313 sessions showed $0.15 was insufficient for Opus-tier accounts; override with `SYKE_ASK_BUDGET`)
- Removed scattered model constants (`DEFAULT_MODEL`, `FULL_MODEL`, `INCREMENTAL_MODEL`); replaced with `ASK_*`, `SYNC_*`, `REBUILD_*` groups

### Added
- ask() now tracks cost/usage metrics to `metrics.jsonl` via `_log_ask_metrics`

## [0.3.2] — 2026-02-18 — "Claude Code Auth: Clean Slate"

Session auth is now the primary path for all Claude Code users.

### Fixed
- MCP config (`~/.claude.json`, Claude Desktop, project `.mcp.json`) no longer bakes in `ANTHROPIC_API_KEY` — MCP subprocess handles it via `config.py` at startup
- Cron/daemon entry no longer embeds `ANTHROPIC_API_KEY` in the crontab line
- `ask()` overrides stale `ANTHROPIC_API_KEY` with `""` when `~/.claude/` is present, forcing session auth (env_patch)
- `ask()` uses Claude Code session auth by default (45b5e8a)
- Daemon LaunchAgent plist no longer bakes in `ANTHROPIC_API_KEY` (429ea36)
- `setup` no longer persists `ANTHROPIC_API_KEY` when `claude login` auth is present (b6e300d)

### Added
- Setup now shows cost notice when API-key-only path is used (~$0.78/build, ~$0.02/ask)
- 67 new tests for `claude_code` and `github_` ingestion adapters (378 total)
- Architecture docs FileTree corrected to match actual filenames

## [0.3.0] — 2026-02-18 — "The Agent Knows Itself"

### Added
- `syke self-update` command: upgrades syke to the latest PyPI release, stop/restart
  daemon around the upgrade, handles pipx/pip/uvx/source install methods gracefully
- `syke/version_check.py`: stdlib-only PyPI version checker with 24-hour disk cache,
  zero new dependencies
- Daemon version drift detection: `_sync_cycle` checks for updates each run, logs a
  WARN line, and inserts a deduped timeline event per new version
- `daemon-status` version display: shows installed version and cached update-available
  notice (zero network cost)
- 16 new tests: `test_version_check.py` (11), `test_cli_self_update.py` (5)

### Changed
- `db.py`: contributor migration invariant comment above `_MIGRATIONS`
- `tests/test_daemon.py`: +2 version-drift tests, fixed `check_update_available` mock,
  log-line-count assertion in `test_sync_cycle_warns_on_update`

## [0.2.9] — 2026-02-17 — "Clean Slate"

First public release with clean git history.

### Changed
- Repository history cleaned for public open source release
- All PII and sensitive development artifacts removed from git history
- Complete test suite maintained (297 tests passing)

### Note
This is the first public release with clean git history. All previous development history has been archived. Previous PyPI versions (0.2.1-0.2.8) are being deprecated.

## [0.2.8] — 2026-02-16 — "Ship-Ready"

Cross-platform daemon, API key persistence, code hardening, docs completeness.

- **feat:** Linux cron backend — `install_cron`, `uninstall_cron`, `cron_is_running` for daemon support on Linux
- **feat:** Platform dispatch — `install_and_start`, `stop_and_unload`, `get_status` auto-select launchd (macOS) or cron (Linux)
- **feat:** Claude Desktop MCP injection works on Linux (`~/.config/Claude/`)
- **feat:** Persist `ANTHROPIC_API_KEY` to `~/.syke/.env` during setup — cron, MCP subprocesses, and non-interactive shells find the key without `.zshrc`
- **fix:** `generate_plist` accepts custom `interval` parameter instead of hardcoded 900
- **fix:** `install_launchd` sets plist to chmod 600 for API key security
- **fix:** `ask()` returns clear message when API key is missing instead of cryptic SDK error
- **fix:** `query_timeline` source list corrected — removed stubs (twitter, youtube), added claude-code
- **docs:** Changelog (4 versions behind → current), contributing, architecture FileTree, README test counts synced
- 297 tests passing (was 276)

## [0.2.7] — 2026-02-16 — "Seamless Agent Install"

Fresh agent installs now work end-to-end without manual debugging.

- **fix:** Auto-unset `CLAUDECODE` env var before perception so Agent SDK works inside Claude Code sessions
- **fix:** Guard Step 4 against `None` profile crash when perception fails
- **fix:** Split setup final summary into three cases: success, API-key-but-no-profile, no-API-key — with actionable instructions
- **feat:** GitHubAdapter auto-detects token via `gh auth token` when `GITHUB_TOKEN` is unset
- **docs:** MCP server instructions now include "First Session" guidance for agents encountering no profile
- **docs:** Getting Started adds "After Setup" section with two-path explanation (with/without API key)
- **test:** 3 new tests — CLAUDECODE env pop, gh token detection, gh CLI fallback (276 total)

## [0.2.6] — 2026-02-16 — "The Two-Step Fix"

Fixes the critical bugs that broke the two-step setup flow (setup without key → add key → rebuild).

- **Fix**: `sync --rebuild` now works when 0 new events — previously early-returned before reaching profile update
- **Fix**: `setup` re-run with API key detects existing events in DB instead of saying "No data sources found"
- **Fix**: `sync` handles nested Claude Code session errors gracefully (same as setup in 0.2.5)
- 273 tests passing

## [0.2.5] — 2026-02-16 — "Smooth Onboarding"

Graceful handling when setup runs inside Claude Code.

- **Fix**: `syke setup` no longer crashes when run inside a Claude Code session — perception is skipped with a clear message, data collection + MCP injection + daemon proceed normally
- Users can run `syke sync --rebuild` from a standalone terminal to build their profile afterward

## [0.2.4] — 2026-02-16 — "Zero Friction"

Agent-native fresh install — one command, zero prerequisites, your AI handles everything.

- **Optional API key**: `syke setup --yes` works without `ANTHROPIC_API_KEY`; perception gracefully skips, profile builds on next `sync` when key is available
- **Absolute path resolution**: MCP configs use fully resolved paths (3-tier detection: source install → pip → uvx) — no more broken relative paths
- **Claude Desktop support**: `setup` now injects Syke into Claude Desktop's MCP config alongside Claude Code
- **Smart daemon plist**: LaunchAgent uses correct syke binary path and injects API key into environment
- **Default user detection**: Falls back to `getpass.getuser()` instead of hardcoded default
- **Sync safety**: Skips perception entirely when no API key is set, preventing confusing errors
- **Test coverage**: Full rewrites for inject, daemon, config, and sync test modules (272 tests passing)
- **Agent-native docs**: README and docs-site reframed around "share with your AI" experience, uvx-first

## [0.2.3] — 2026-02-16 — "The Spider's Web"

ALMA meta-learning experiments tracked in repo, incremental perception with delta merge, comprehensive doc audit across all public surfaces.

- ALMA meta-learning code tracked in `experiments/perception/` (7 files: strategy evolution, eval framework, reflection)
- Incremental perception: delta-only profile updates via Sonnet (~$0.08 vs $0.78 full rebuild)
- Delta merge logic preserves unchanged fields, ignores falsy values
- Sync improvements: minimum event threshold, `--force` and `--rebuild` flags
- SQLite: busy_timeout + backlog fixes, uuid dependency resolved
- Viz site: product/research page split, ALMA learning component, interactive perception timeline
- Doc audit: fixed stale numbers across README, CLAUDE.md, CONTRIBUTING.md, docs site, and viz (257 tests, 8 MCP tools, 6,500 LOC, 3,225 events)
- PII scrub: removed internal docs and personal data from public repo

## [0.2.2] — 2026-02-15 — "The Right Database"

Harden the MCP push pipeline and fix DATA_DIR resolution.

- **Fix**: DATA_DIR always resolves to `~/.syke/data` regardless of install method
- **Fix**: Catch `TypeError` in MCP `push_event()` and `push_events()` JSON parsing
- **Fix**: Guard non-dict elements in `push_batch()` to prevent `AttributeError` crash
- **Fix**: Validate metadata round-trips correctly through string→dict parsing in tests
- **Harden**: MCP push pipeline validates metadata types, timestamp formats, and JSON structure
- **Harden**: Reject non-dict metadata (lists, scalars) with clean error messages
- **Logging**: `IngestGateway.push()` emits info log on successful insert

Tests: 244 pass (up from 233).

## [0.2.1] — 2026-02-15 — "The Agent Remembers (CI Fix)"

Patch release to fix CI test failures when ANTHROPIC_API_KEY is not set.

- **Fix**: Skip `ask()` MCP test when no API key present (fixes CI failures)
- **Docs**: Position `ask()` as recommended (not required) in MCP server instructions
- **Docs**: Add comprehensive API key setup instructions to README
- **Clarity**: Document that core 6 MCP tools work without API key, `ask()` requires it

Tests: 233 pass with API key, 232 pass + 1 skip without API key.

## [0.2.0] — 2026-02-15 — "The Daemon Awakens"

Background sync daemon ships as a core feature with automatic cost optimization.

**Daemon**
- Background sync daemon now ships with pip package
- New CLI commands: `daemon-start`, `daemon-stop`, `daemon-status`
- Integrated into `syke setup` with interactive prompt
- Automatically syncs every 15 minutes (configurable)

**Cost Optimization**
- Skip perception when no new events exist (saves ~$0.50 per daemon cycle)
- Cap profile size in incremental updates to prevent unbounded growth

**MCP Server**
- Add async `ask()` tool for natural language queries about the user
- Timeline and search tools now return summaries by default (pass `summary=false` for full content)

**Perception**
- Add `world_state` field: precise map of user's current projects and status
- Agentic perception now default for `syke sync` (pass `--legacy` for single-shot mode)

## [0.1.1] — 2026-02-14 — "The System Remembers"

Docs, CI, and open source infrastructure.

- Nextra docs site with full architecture, reference, and guide pages
- GitHub Actions CI (test matrix, publish workflow)
- GitHub templates (issue, PR)
- tbump release infrastructure
- Version bump to 0.1.1

## [0.1.0] — 2026-02-13 — "The System Sees"

The distribution layer. Syke can now be consumed by any MCP client.

- MCP server with 7 tools (get_profile, query_timeline, search_events, push_event, etc.)
- Push/pull federated model — any MCP client can read and write context
- Content filter: pre-ingestion stripping of credentials and private messages
- 4 output formats: JSON, Markdown, CLAUDE.md, USER.md
- Interactive viz site deployed to Vercel
- 212 tests across 14 files, all mocked
- Documentation overhaul

## [0.0.4] — 2026-02-12 — "The Agent Evolves"

ALMA-inspired strategy evolution. The agent learns which searches work.

- Trace analysis: deterministic reflection labels searches as productive or wasted
- Strategy evolution across 12 runs on real data
- Peak quality: 94.3% at $0.60/run (67% cheaper than legacy)
- 4-way benchmark: Legacy vs Agentic vs Multi-Agent vs Meta-Best
- Eval framework with per-dimension scoring

## [0.0.3] — 2026-02-11 — "Three Agents, One Identity"

Multi-agent orchestration. Three minds, one synthesis.

- 3 Sonnet sub-agents: Timeline Explorer, Pattern Detective, Voice Analyst
- Opus synthesizes findings into final profile
- Agent SDK's AgentDefinition for delegation and tool scoping
- 100% source coverage (up from 67% single-agent)

## [0.0.2] — 2026-02-10 — "The Agent Explores"

Agent SDK rewrite. The agent can now *explore*, not just *receive*.

- 6 custom MCP tools for interactive exploration
- Coverage-gated submission via PermissionResultDeny hooks
- Agent makes 5-12 targeted tool calls per run
- Quality improves through hypothesis testing

## [0.0.1] — 2026-02-09 — "Foundation"

Core pipeline. From raw data to identity.

- Claude Code adapter (dual-store, DFS path resolver)
- ChatGPT ZIP export parser
- GitHub REST API adapter with pagination
- Gmail OAuth adapter (gog CLI + Python fallback)
- SQLite timeline with WAL mode
- Legacy perception: single-shot Opus with 16K extended thinking
- Pydantic models, Click CLI, Rich terminal output
