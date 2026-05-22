# Changelog

All notable changes to Syke are documented here.

## [Unreleased]

- Added a local `scripts/release-candidate.sh` gate so maintainers prove a
  candidate before pushing, tagging, or publishing.
- Aligned maintainer docs around the release order: local candidate proof,
  pushed GitHub Actions confirmation, version/changelog bump, tag candidate
  proof, then publish workflow.
- Hardened MEMEX/timeline truth surfaces: timeline ordering now sorts mixed
  timestamp offsets by instant, and trace-derived memory touches no longer
  overload canonical memory update counters.
- Made the Linux daemon proof explicit through a user-systemd smoke gate while
  keeping Linux support scoped to hosts where `systemd --user` is available.
- Tightened release-facing README wording around supported harnesses and
  self-maintaining memory behavior.

## [0.5.6] — 2026-05-10

Patch — fresh setup, agent install, and timeline onboarding hardening.

## [0.5.4] — 2026-04-30

Patch — widen the Pi retry-settlement grace.

- `_RETRY_SETTLEMENT_GRACE_SECONDS` raised from 0.2s to 1.0s in
  `syke/llm/pi_client.py`. The grace window is how long the synthesis path
  waits after a retryable `agent_end` (e.g. 429) for an `auto_retry_start`
  to arrive before declaring the cycle failed. 200ms was tight under
  network or scheduling jitter — slow retries got mis-classified as
  failures. The cost of widening is at most extra wall-time for the rare
  case of a *terminal* retryable error with no retry coming.

## [0.5.3] — 2026-04-30

The synthesis-coherence release. Trace analysis of post-redesign cycles
showed the agent doing 2.6× the verification work of the pre-redesign
baseline, driven by an open-ended synthesis instruction and missing
"what changed since last wake" signal.

- **Synthesis prompt rewritten** to frame MEMEX as the agent's prior, not
  external state. Agent is told not to re-derive numbers, timestamps, or
  claims already in MEMEX. Adds `syke.db is the source of truth, MEMEX is
  its projection`. Drops the "Read the harnesses" / "Check what's already
  known" / "Decide what's durable" license-to-explore lines that traces
  showed inviting cross-harness verification work.
- **Cycle gap surfaced in `<now>` block.** `Last cycle: …` line now appends
  a relative gap label (e.g. `(15 min ago)`, `(2 h ago)`, `(3 d ago)`) so
  the agent can size the cycle's effort against how much real time has
  passed since the previous wake.
- **`format_gap()` helper** added to `syke.runtime.psyche_md` for shared
  use across production and replay.

## [0.5.2] — 2026-04-22

The local-runtime hardening release.

This release turns Syke from a research-era memory prototype into a cleaner
product surface: one local memory store, a Pi-native runtime, source selection
as a persisted contract, honest daemon status, and a much smaller release
artifact. Replay and benchmark work now live outside this repo.

PM summary:

- Users get a clearer first-run path: install, setup, doctor, memex, ask.
- Operators get safer runtime behavior: bounded child-process env, sandbox
  cleanup, daemon IPC/status visibility, and release smoke tests.
- Agents get one command surface: `syke memex`, `syke ask`, `syke record`.
- Maintainers get a release checklist and preflight that build, install, smoke,
  and test the package before tagging.

The old copy pipeline, Python adapter infrastructure, and dual-database model
are gone. The agent now reads harness data directly via adapter markdowns and
bash/sqlite3. The wheel excludes docs, scripts, tests, research, GitHub
workflow files, and replay-lab internals.

### Product Surface

- Command rename: `syke context` is now `syke memex` across CLI, docs, tests,
  and distributed skill/capability text.
- `README.md` now presents the new product story: local-first memory, Pi runtime,
  source selection, daemon safety, and replay separation.
- Maintainer release gates and open loops for the 0.5.2 line were kept
  internal; release verification is driven by local preflight scripts.
- `docs/CURRENT_STATE.md` captures the current runtime contracts for future
  agents and maintainers.

### Architecture

- **Direct harness reads** — the agent reads harness data directly via adapter
  markdowns. No Python adapter ABC, no factory, no watcher, no copy pipeline.
- **Single database** — `events.db` merged into `syke.db`. One file holds
  memories, links, events, cycle records, and rollout traces.
- **Workspace collapsed** — `~/.syke/data/{user}/` flattened to `~/.syke/`.
  MEMEX, PSYCHE, adapters, sessions all live at the top level.
- **PSYCHE.md** — agent identity contract injected into every ask and synthesis
  prompt. Replaces the old AGENTS.md.
- **Unified prompt** — PSYCHE + MEMEX + skill markdown injected for both ask
  and synthesis paths via shared `build_prompt()`.
- **Source selection contract** — setup/sync can persist selected harness
  sources, and runtime paths reuse that persisted selection.
- **Runtime/replay split** — replay-lab moved out to a sibling repo and this
  repo is restricted to the product/runtime surface.

### Sandbox

- **OS-level sandbox** — Pi runs inside macOS `sandbox-exec` with deny-default
  reads. Catalog-scoped per-user harness paths are the only allowed reads
  outside system directories. Writes restricted to `~/.syke/` + temp.
- Network outbound is open (API calls need it).
- Temporary sandbox profiles are cleaned up after runtime stop and after launch
  failure.

### Runtime And Auth

- **Pi-native execution** — Pi is the canonical runtime for ask, synthesis, and
  daemon work.
- **Bounded subprocess env** — Pi node scripts, OAuth login, and runtime launch
  receive only the required environment instead of inheriting the full host
  shell.
- **Owner-only Pi state** — `~/.syke/pi-agent` and migrated auth/settings/model
  files are hardened to owner-only permissions.
- **Rubric bridge** — replay-lab can pass `SYKE_RPC_RUBRIC_SPEC_PATH` to build a
  dynamic judge schema while Syke falls back to the legacy v1 schema when absent
  or invalid.

### Daemon

- **Synthesis atomicity** — memex sync, cursor advance, and cycle completion
  commit in one transaction. DB is never left with new memex but old cursor.
- **IPC drain** — clean shutdown drains pending IPC messages before exit.
- **Synthesis timeout** — cycles that exceed the configured timeout are
  terminated and marked failed.
- **Deeper health checks** — runtime liveness (Pi process alive, IPC reachable),
  deadlock detection via busy flag.
- **Concurrent synthesis lock** — cross-process `fcntl` lock prevents two
  synthesis cycles from running simultaneously.
- **Honest daemon controls** — start/stop/self-update fail closed when process,
  registration, or IPC state is degraded instead of reporting false success.
- **Runtime as critical health** — daemon health now treats runtime reachability
  as release-critical alongside Python and database checks.

### Distribution

- **Atomic writes** — memex export and skill installs use temp+rename pattern.
- **Conditional re-export** — `memex_updated` flag flows from synthesis through
  daemon to distribution. Unchanged memex is not re-exported.
- **Dead code removed** — `claude_include_ready`, `codex_memex_ready` fields
  and their log checks deleted.

### Self-observation

- **Rollout traces always on** — dead env var gate removed. Full prompts,
  responses, thinking, tool calls persisted per ask and synthesis.
- **TCC permission blocks** surfaced in `syke doctor`.

### Removed

- `SenseWriter`, `SenseWatcher`, `SQLiteWatcher`, `JsonlTailer` — watcher
  infrastructure (agent reads directly now)
- `ObserveAdapter` ABC, `DynamicAdapter`, factory-generated Python adapters
- `events.db` — merged into `syke.db`, then replaced by direct reads
- `sync_source()`, `run_sync()`, ingest pipeline
- `force` param, `SYNC_EVENT_THRESHOLD`, `count_events_since`, `_reconcile`
- `INGESTION_*`, `SENSE_*`, `REGISTRY_*` trace constants
- `notes.md`, `cursor.md` workspace stubs
- `_record_completion()`, `_record_pi_metrics()`, `_record_pi_tool_observations()`
  stubs in synthesis
- OSS boilerplate docs (SECURITY, CODE_OF_CONDUCT, CONTRIBUTING, RELEASING,
  issue/PR templates, TESTING, CLI_UX_SPEC)
- Dead factory skill (`syke/observe/skills/factory.md`)
- LiteLLM — fully removed from codebase

### Fixed

- Stale `~/.syke/data/{user}/` paths in SKILL.md, PLATFORMS.md, CHANGELOG
- `test_install_surface` seed file names (.py → .md)
- Distribution test assertions for removed fields
- Bootstrap message guides agent on first run instead of empty prompt
- `syke ask --json` and `--jsonl` now exit non-zero with structured errors when
  backend metadata reports a runtime error.
- Invalid persisted source selections now fail closed instead of broadening
  runtime scope.
- Release preflight now uses the project Python selected by `uv`, avoiding
  accidental Python 3.11 smoke environments for a Python 3.12+ package.

### Added

- `py.typed` PEP 561 marker
- macOS CI workflow
- Test coverage for synthesis timeout, IPC drain, build_prompt,
  initialize_workspace, memex_updated distribution flow
- 8 adapter markdown seeds for all supported harnesses

### Validation

- `uv run pytest tests -q`: 428 passed, 8 skipped
- `uv run ruff check`: passed
- `uv run ruff format --check`: passed
- `bash scripts/release-preflight.sh`: passed
- release preflight covers targeted runtime/CLI tests, wheel build, isolated
  wheel smoke, isolated `uv tool install` smoke, and foreground daemon smoke

---

## [0.5.1] — 2026-04-04

The refactor release. Everything that was prototyped in 0.5.0 is now modular,
validated, and documented. The CLI is no longer a monolith. Observe ships real
adapters instead of generating them on-the-fly. Auth delegates entirely to Pi.
The daemon is harder to break. 142 files changed, 50 commits.

### CLI

The 3,391-line `cli.py` monolith is gone. In its place:

- **8 command modules** in `syke/cli_commands/` — ask, auth, config, daemon,
  maintenance, record, setup, status
- **11 support modules** in `syke/cli_support/` — ask output, auth flows,
  daemon state, doctor, exit codes, installers, providers, rendering, setup
- **`syke/entrypoint.py`** — one entry point, commands grouped into Primary
  and Advanced sections in help output
- **Unified exit codes** (0–6) in `syke/cli_support/exit_codes.py` — success,
  failure, usage, auth, runtime, trust, data
- **Agent mode** — `syke setup --agent` returns structured JSON for
  non-interactive automation
- **Dashboard** — bare `syke` invocation shows a quick status overview

### Observe

TOML descriptors and dynamic adapter generation are replaced by a seed-first
architecture:

- **`syke/observe/catalog.py`** — centralized `SourceSpec` dataclass catalog
  replaces scattered `.toml` descriptor files
- **8 shipped seed adapters** in `syke/observe/seeds/` — claude-code, codex,
  copilot, cursor, gemini-cli, hermes, opencode, antigravity. Pre-built,
  tested, debuggable Python, not LLM-generated
- **`syke/observe/validator.py`** — strict validation pipeline (path scoping,
  session sampling, ingest stability) runs before any adapter is deployed
- **Three-step bootstrap** — use existing deployed adapter if valid → fall back
  to shipped seed → generate via factory only if needed
- **Simplified factory** — one unified skill (`syke/observe/skills/factory.md`)
  replaces three separate generation skills
- **Simplified registry** — two-step lookup (deployed → seed), no fallback
  chains or DynamicAdapter wrapper

### Auth

Syke no longer owns a provider registry or auth store. Pi owns provider truth:

- **Deleted** — `syke/llm/auth_store.py`, `syke/llm/codex_auth.py`,
  `syke/llm/providers.py`
- **`syke/pi_state.py`** — Syke-owned Pi agent state management under
  `~/.syke/pi-agent/` (auth.json, settings.json, models.json)
- **Audit trail** — every credential and provider mutation logged to
  `~/.config/syke/pi-state-audit.log`
- **Legacy migration** — auto-migrates `~/.pi/agent/` → `~/.syke/pi-agent/`
  on first access
- **`syke auth login`** — ships for Pi-native OAuth providers (was planned,
  now implemented)

### Daemon

- **fcntl lock** — file-based exclusive lock at `~/.config/syke/daemon.lock`
  prevents duplicate daemon instances
- **Adaptive retry** — failed cycles retry in 5 seconds instead of waiting the
  full interval; failed syntheses do not trigger distribution
- **Tag-based logging** — symmetric `DaemonFormatter` with module-mapped tags
  (SYNC, OBS, SYNTH, DIST, PI, IPC, ASK, COST)
- **IPC protocol v1** — versioned protocol, new `runtime_status` message type
  for querying daemon runtime health, `DaemonIpcBusy` exception with fallback
  to direct runtime, auto-recovery of lost IPC sockets

### Distribution

- Simplified to memex export + SKILL.md installation + native harness wrappers
  (Cursor custom command, Copilot agent, Antigravity workflow)

### Docs

Full refresh across all documentation to match shipped code:

- ARCHITECTURE.md — file map, dependency graph, observe/CLI sections rewritten
- SECURITY.md — credential paths updated for Pi-native auth model
- RUNTIME_AND_REPLAY.md — daemon locking, logging, IPC protocol documented
- PROVIDERS.md — audit trail, legacy migration, env var overrides added
- CONFIG_REFERENCE.md — 7 runtime env vars documented
- CLI_UX_SPEC.md — updated to reflect 0.5.1 shipping state
- guide/agent-setup.md — stale Node.js requirement removed

### Validation

- Ruff lint: clean
- 128 install/runtime tests passed, 4 skipped
- 66 CLI release-path tests passed
- Wheel build, twine check, smoke artifact install, smoke tool install: all pass

---

## [0.5.0] - 2026-04-01

Syke 0.5.0 is the release where the memory agent becomes a real local system.
Pi is now the runtime. Observe is now a deterministic sensor boundary. The memory
contract is explicit end to end: `syke.db` holds learned state, `MEMEX.md` is
the routed projection, and the agent reads harness data directly via adapter
markdowns. The result is a tighter, more inspectable, more
portable memory agent that can run locally and distribute itself through the CLI
and skill surfaces power users already live inside.

### Highlights
- Pi replaces the older proxy-heavy runtime path and becomes the canonical agent
  execution engine for ask, synthesis, daemon work, and replay.
- The memory system now runs around a clean authority split:
  - `syke.db` for learned memory and cycle state
  - `MEMEX.md` for the current navigable projection
  - Harness data read directly via adapter markdowns
- Observe becomes the trusted ingest boundary. It captures harness activity
  mechanically before the agent starts reasoning over it.
- Setup, sync, ask, daemon, replay, and distribution now point at one shared
  workspace contract instead of drifting between legacy paths.

### Added
- Pi-native runtime surfaces:
  - `syke.llm.pi_client`
  - `syke.llm.pi_runtime`
  - `syke.llm.backends.pi_ask`
  - `syke.llm.backends.pi_synthesis`
  - `syke/llm/backends/skills/pi_synthesis.md`
- A first-class runtime workspace layer in `syke/runtime/` with:
  - exact DB binding
  - workspace snapshot refresh
  - sandbox policy
  - AGENTS/attachment projection support
- A full Observe harness stack in `syke/observe/` with:
  - descriptors
  - adapter registry
  - JSONL and SQLite runtime watchers
  - dynamic adapter generation
  - bootstrap and healing paths
- New operator and architecture docs:
  - `docs/CURRENT_STATE.md`
  - `docs/RUNTIME_AND_REPLAY.md`
  - `docs/MEMEX_IN_USE.md`
- Release artifact verification:
  - `scripts/check_release_tag.py`
  - `scripts/smoke-artifact-install.sh`

### Changed
- Pi is now the only runtime. Runtime selection no longer drifts across older
  backend stories.
- Setup now follows an inspect-first local plan:
  - surfaces detected providers and sources
  - asks for consent where local writes matter
  - can bootstrap adapters before first ingest
  - can run first synthesis and enable background sync from the same flow
- Ask and daemon behavior are substantially tighter:
  - ask can route through daemon IPC first
  - warm runtime reuse and cold-start behavior are measured directly
  - workspace refresh is tracked and skipped when safe
- The Observe boundary is simpler and stronger:
  - harness activity is captured into an append-only ledger
  - adapter generation is held to the current session contract
  - file watcher restart behavior is durable across warm restarts
- Distribution is now deliberately local-first and CLI-first:
  - `syke ask`
  - `syke memex`
  - `syke record`
  - `syke status`
  - `syke doctor`
  - exported memex and installed `SKILL.md` files as downstream surfaces
- OpenCode joins the default skill distribution targets alongside the existing
  supported harness paths.
- The public docs now describe the current product shape directly, including the
  federated memex model, the runtime contract, and the supported harness path.
- The project is now licensed under `AGPL-3.0-only`.

### Fixed
- Stale workspace and snapshot corruption paths in the Pi runtime and synthesis
  loop
- Synthesis locking and memex sync edge cases
- Ask IPC fallback and machine-readable output behavior
- Watcher restart churn and startup replay edge cases
- Release packaging drift, stale modules in wheels, and artifact-install
  validation gaps
- Test isolation issues that were masking release-readiness regressions

### Removed
- Legacy runtime and compatibility surfaces that no longer matched the Pi-native
  system
- Old proxy and LiteLLM-heavy paths from the hot runtime loop
- Older web and docs-site surfaces that no longer reflected the product
- A large amount of stale experiment and compatibility code that kept leaking
  branch-era complexity into the release surface

### Validation
- Full test suite: `561 passed, 10 skipped`
- Release build, `twine check`, and smoke artifact install all pass
- CI, publish, and PyPI release gates are green for `v0.5.0`


## [0.4.6] — 2026-03-12 — "The Gateway"

Multi-provider LLM gateway, synthesis pipeline rewrite, CLI overhaul, documentation rethink. 48 commits, 52 files changed.

### Added
- **LiteLLM gateway** (`syke/llm/litellm_config.py`, `litellm_proxy.py`) — 10 providers through a unified dispatch layer. Azure, Azure AI Foundry, OpenAI, OpenRouter, Ollama, vLLM, llama.cpp, Kimi, z.ai alongside existing Codex + Claude login
- **Provider-specific env resolution** (`syke/llm/env.py`) — each provider gets explicit env var wiring, no silent fallbacks
- **`[providers]` config section** — TOML configuration for per-provider settings (endpoint, model, base URL)
- **Azure AI Foundry provider** — `azure-ai` provider spec for Azure AI Foundry models
- **Memory ID prefix matching** — `get_memory()` and `search_memories()` support prefix lookup (synthesis agent writes truncated UUIDs)
- **Python 3.14 in CI** — test matrix expanded from 3.12/3.13 to 3.12/3.13/3.14
- **Lint gate on publish** — `publish.yml` now runs ruff format + check before tests, preventing PyPI push with lint failures

### Fixed
- **LiteLLM streaming crash** — v1.82.0 `reasoning_content` block type mismatch (block says "text", delta says "thinking_delta") crashed Claude Agent SDK. Patched block type alignment
- **Synthesis pipeline** — rewritten with `finalize_memex` tool contract + Stop hook enforcement. Agent must call the tool, hook terminates the loop. No ambiguous completions
- **Setup flow** (5 bugs) — daemon always installing, race condition after daemon start, misleading "0 sessions" display, codex credential verification, provider picker default
- **Auth display** — shows key length only (`●●● (84 chars)`), no character leakage
- **Config show** — displays resolved effective state (file + defaults + env overrides)
- **Provider picker** — Codex first (recommended), Claude login last with account ban warning
- **CI lint errors** — B904 `raise SystemExit` in except clause, F401 unused import. CI was broken since Mar 8

### Changed
- **README** rewritten — positioning, architecture diagram, Persona benchmark comparisons, research references (RLM, ALMA, ACE, DSPy, GEPA)
- **ARCHITECTURE.md** overhauled — design thesis, graph section, ASCII diagrams, provider auth guide (459 lines)
- **MEMEX_EVOLUTION.md** rewritten — research positioning paper with emergence evidence from 111 memex versions
- **Hermes skill version** now dynamic from `syke.__version__` instead of hardcoded
- **Version tags removed** from doc headers (CONFIG_REFERENCE, PROVIDERS) — no more stale version strings
- **Dead code purge** — deleted UserProfile, ActiveThread, VoicePattern models, formatters.py, experiments/perception/ (net -5,000 lines)

### Tests
- 337 passing, 12 skipped (was 286)
- New: `test_litellm_config.py`, `test_litellm_proxy.py`, LiteLLM integration matrix, auth backward compat, CLI auth set tests

### Infrastructure
- Publish workflow gates on lint + tests before PyPI push
- Branch protection requires test (3.12), test (3.13), test (3.14)
- TCC-protected binary path rejection in daemon LaunchAgent


## [0.4.5] — 2026-03-07 — "The Blueprint"

Configuration file system. All 70+ hardcoded values now configurable via `~/.syke/config.toml` — models, budgets, paths, sources, privacy filters. TOML format, zero new dependencies.

### Added
- **Config file** (`~/.syke/config.toml`) — TOML-based configuration with 12 typed sections: identity, provider, models, sources, synthesis, daemon, ask, rebuild, distribution, privacy, paths
- **`syke config` CLI** — `syke config init` generates commented config, `syke config show` displays effective config (merged defaults + file + env), `syke config show --raw` prints TOML, `syke config path` prints location
- **Per-task model selection** — `[models]` section: pick different models for synthesis (cheap), ask (interactive), rebuild (expensive). Forward-compatible with `provider/model` format for future multi-provider routing
- **Configurable paths** — data dir, auth file, ingestion source dirs (Claude Code, Codex, ChatGPT export), distribution targets (CLAUDE.md, skill dirs, Hermes home)
- **Config precedence** — env var > config.toml > hardcoded default. Existing env var overrides still work, config file is optional
- **22 new tests** — defaults, TOML parsing, nested sections, hyphen-to-underscore mapping, unknown key handling, malformed file recovery, path expansion, template roundtrip, full schema validation

### Changed
- `syke/config.py` rewritten to load from config file at startup, all module-level constants now sourced from `SykeConfig` dataclass with env var overrides
- 7 modules wired to centralized config paths: ingestion (claude_code, codex), distribution (context_files, hermes), LLM (auth_store, codex_proxy), sync, daemon
- `SYNC_EVENT_THRESHOLD` and `DAEMON_INTERVAL` moved from local definitions to config system
- Test suite: 264 → 286 tests

### Technical
- Built on `tomllib` (Python 3.11+ stdlib) — zero new dependencies
- 12 frozen dataclasses for type-safe config access
- `get_type_hints()` for correct nested dataclass resolution under `from __future__ import annotations`
- Template-based config generation (TOML write without write library)


## [0.4.4] — 2026-03-06 — "The Switchboard"

Model-agnostic multi-provider support. Use your existing AI subscriptions — ChatGPT Plus, Claude Max, OpenRouter, z.ai — and Syke works with any of them.

### Added
- **Multi-provider core** (`syke/llm/`) — provider registry, resolution with precedence (CLI flag > env > auth.json > auto-detect), environment isolation per provider
- **Codex translator proxy** (`syke/llm/codex_proxy.py`) — local HTTP server translates Claude Messages API to OpenAI Responses API, enables ChatGPT Plus via Codex CLI
- **Auth CLI** — `syke auth set <provider> --api-key` (stores + auto-activates), `syke auth use`, `syke auth status` with provider discovery
- **Interactive provider picker** — arrow-key selection menu in `syke setup` (via `simple-term-menu`), shows all providers with status tags, falls back to numbered list in non-TTY environments
- **Credential leak prevention** — `clean_claude_env()` strips `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` to prevent cross-provider leakage
- **Provider-aware doctor** — `syke doctor` shows resolved provider and credential status
- **Codex ingestion** — `syke sync` imports Codex CLI sessions from `~/.codex/`

### Changed
- `syke setup` always shows provider picker — no silent auto-select, even with auto-detected auth
- Setup no longer gates on `claude login` — works with any provider from first run
- Removed `syke login` alias (was pure wrapper with zero unique logic)
- Dashboard shows resolved provider instead of Claude auth status
- Test suite pruned from 276 → 261 (removed duplicates and low-signal assertions)

### Infrastructure
- **Ruff linting enforced** — `ruff check` + `ruff format --check` in CI, rules: E, F, I, UP, B, line-length 100, target py312
- **CI pipeline evolved** — 3 jobs (lint → test matrix 3.12/3.13 → build), reusable `_tests.yml` workflow, concurrency cancellation, pip caching, minimal permissions, timeouts
- **Publish workflow** reuses `_tests.yml` for test gate, adds build verification before PyPI upload
- **Pre-release doc audit** — SKILL.md, README, CONTRIBUTING.md, context preamble updated for multi-provider; stale version refs and claude-login assumptions fixed across 9 files

### Supported Providers
| Provider | Auth | Method |
|----------|------|--------|
| `claude-login` | Claude Max/Team/Enterprise | Session auth (no API key) |
| `codex` | ChatGPT Plus/Pro | Reads `~/.codex/auth.json` |
| `openrouter` | OpenRouter | API key |
| `zai` | z.ai | API key |


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
- `syke memex` — dump current memex to stdout
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
- **Storage**: SQLite + FTS5 + WAL. BM25 full-text search over memories and events. Single file at `~/.syke/syke.db`.
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
