# Syke CLI UX Spec

Status: draft  
Date: 2026-03-28

This document defines the next Syke CLI surface for onboarding, auth, trust, and machine use.

It is intentionally minimal in command count, but complete in contract. Syke should feel simple for a human on first run and deterministic for another agent or script calling it headlessly.

This spec is informed by the current Syke implementation plus current official docs across the AI CLI ecosystem this week: Codex CLI, Claude Code, Gemini CLI, Goose, and Aider.

## Positioning

Syke is not a general-purpose coding shell.

Syke is memory infrastructure:

- observe activity across harnesses
- maintain learned local memory
- distribute that memory back into agent environments
- answer deeper grounded questions through the runtime

The CLI should express that clearly.

## Design Goals

1. Make first run obvious.
2. Make auth low-friction.
3. Make trust explicit.
4. Keep the top-level command set small.
5. Give agents and scripts a stable machine contract.
6. Keep `MEMEX.md` canonical while supporting harness-native projections.
7. Preserve local-first and privacy-first defaults.

## Non-Goals

- Do not turn Syke into a persistent chat shell.
- Do not copy coding-agent slash-command UX by default.
- Do not expose provider complexity on the happy path.
- Do not make harness-specific files the source of truth.

## Ecosystem Signals

As of 2026-03-28, the leading AI CLIs are converging on a few patterns:

- browser/account-first auth for humans, API/env auth for automation
- explicit trust or permission surfaces
- separate interactive and non-interactive flows
- machine-readable outputs for automation
- project instruction files as an interop layer
- resumable sessions and protocol-based integration becoming more common

Syke should adopt the useful parts of that pattern without pretending to be a coding agent.

## Product Modes

Syke has two deliberate modes.

### 1. Human mode

Human mode is the default. It should optimize for:

- quick install
- clear setup
- readable output
- explicit consent for personal data sources
- obvious health/status checks

### 2. Agent mode

Agent mode is non-interactive and deterministic. It should optimize for:

- no prompts
- stable stdout/stderr behavior
- JSON or JSONL output
- stable exit codes
- exact flags for auth, provider, and format

## Top-Level Command Surface

Visible top-level commands should be:

- `setup`
- `ask`
- `context`
- `record`
- `status`
- `sync`
- `auth`
- `doctor`

Advanced commands should remain available, but moved out of the primary surface in help and docs:

- `daemon`
- `config`
- `connect`
- `cost`
- `install-current`
- `dev`

This makes the product legible:

- install memory
- query memory
- inspect memory
- sync memory
- verify memory

## Command Taxonomy

### `syke setup`

Purpose: first-run onboarding and repair of the standard local contract.

Human contract:

- detect local sources
- explain what will be ingested
- explain what will be written
- choose auth
- choose provider if needed
- bootstrap Observe adapters if needed
- install the background loop where supported
- end with a clean summary

Agent contract:

- `syke setup --yes --provider <id> --json`
- never prompt
- fail with actionable machine-readable errors

Required setup summary:

- active user
- auth mode
- provider
- trusted sources
- trusted targets
- data location
- daemon state
- next three commands

### `syke ask`

Purpose: deep grounded question answering over the current Syke store and runtime workspace.

Human contract:

- stream answer to stdout
- stream reasoning/traces/tool notes to stderr
- print concise metrics footer to stderr

Agent contract:

- `--json` for one final structured result
- `--jsonl` for event stream plus final result
- stable schema
- no ANSI escapes in machine modes

Minimum structured result:

```json
{
  "ok": true,
  "question": "string",
  "answer": "string",
  "provider": "string",
  "duration_ms": 0,
  "cost_usd": 0.0,
  "input_tokens": 0,
  "output_tokens": 0,
  "tool_calls": 0
}
```

For `--jsonl`, event types should be explicit:

- `status`
- `thinking`
- `tool_call`
- `text`
- `result`
- `error`

### `syke context`

Purpose: fast read of current distributed memory.

Contract:

- always local
- no model call
- machine-readable by default when asked

Formats:

- `--format markdown`
- `--format json`

This is the universal read surface other harnesses can rely on when they cannot access the live store.

### `syke record`

Purpose: append user or agent observations into the evidence system.

Contract:

- plain text mode
- single JSON event mode
- JSONL batch mode
- clear duplicate and filter semantics

`record` is already close to the right shape. It mainly needs better documentation and alignment with the trust model.

### `syke status`

Purpose: one compact operational dashboard.

Human status should show:

- active provider
- auth state
- auth source
- model
- endpoint
- daemon state
- source counts
- last ingest
- memex state
- trusted sources
- trusted targets

Agent status should support:

- `--json`

Minimum structured status:

```json
{
  "ok": true,
  "user": "string",
  "provider": "string",
  "daemon": {
    "running": true
  },
  "sources": {},
  "last_event_at": "iso8601",
  "memex": {
    "present": true,
    "memory_count": 0
  },
  "trust": {
    "sources": [],
    "targets": []
  }
}
```

### `syke sync`

Purpose: explicit one-shot observe + synthesize cycle.

Human contract:

- tell the user what changed
- show which sources ran
- say whether synthesis ran or was skipped

Agent contract:

- `--json`
- stable result schema

### `syke auth`

Purpose: manage identity to the runtime provider layer without making the user think about low-level configuration first.

Target subcommands:

- `syke auth login`
- `syke auth status`
- `syke auth use <provider>`
- `syke auth set <provider> ...`
- `syke auth unset <provider>`

Rules:

- `login` is the human entry point
- `set` is the advanced/manual entry point
- `status` must support `--json`
- `status` must show selected provider, auth source, model, and endpoint explicitly
- setup should prefer `login` flow rather than making the user think in terms of raw providers too early

### `syke doctor`

Purpose: health check for install, auth, storage, runtime, and trust surfaces.

Human contract:

- green/yellow/red checks
- concise fix hints

Agent contract:

- `--json`
- `--network` remains optional for active provider testing

Minimum doctor sections:

- binary/install
- auth
- provider resolution
- local stores
- workspace projection
- daemon
- network check when requested

## Auth UX

Auth should split cleanly by user intent.

### Human happy path

Preferred order:

1. `syke setup`
2. `syke auth login`
3. Syke detects an already available account-backed provider when possible
4. If not, Syke offers advanced provider setup

The first-run screen should present provider choices in this order:

- recommended logged-in account path
- local runtime path
- API key path

That keeps first run simple without removing flexibility.

### Agent and CI path

Supported paths:

- `--provider`
- `SYKE_PROVIDER`
- `syke auth set ...`
- provider env vars

Rules:

- no interactive login prompt in non-TTY mode
- always return machine-readable status when requested

## Trust UX

Trust must become a first-class concept.

Syke works with personal local data. Setup and status should describe trust in terms users can understand:

- trusted sources: places Syke may read from
- trusted targets: places Syke may write projections into

This should not be hidden as implementation detail.

Target trust commands do not need to ship immediately, but the model should be reflected in setup, status, doctor, and config.

Minimum trust state:

- source name
- local path
- enabled or disabled
- last confirmed time

## Distribution Model

`MEMEX.md` remains canonical.

Harness-native files are projections derived from that canonical memory surface:

- `CLAUDE.md`
- `GEMINI.md`
- `AGENTS.md`
- skill-like injected files

That means:

- the DB is authoritative learned state
- `MEMEX.md` is the canonical routed artifact
- harness files are distribution sinks

This is the correct interop story. It preserves Syke's identity while still fitting the ecosystem's instruction-file conventions.

## Help And Discoverability

Help output should teach the product in one screen.

Top-level help should have:

- one-sentence product definition
- primary commands
- advanced commands collapsed into a second section
- three example commands

Example shape:

```text
Primary Commands:
  setup     Connect sources and initialize local memory
  ask       Ask a grounded question using the current memory state
  context   Print the current memex
  record    Add a note or observation
  status    Show memory system status
  sync      Run one observe + synthesize cycle
  auth      Manage provider login and credentials
  doctor    Verify install and health

Advanced:
  daemon
  config
  connect
  cost
```

## Output Rules

These rules should hold across the CLI.

- human-readable results on stdout unless a command is explicitly diagnostic
- progress, traces, warnings, and metrics on stderr
- `--json` returns one JSON object on stdout
- `--jsonl` returns newline-delimited events on stdout
- non-interactive mode never opens prompts
- ANSI styling is disabled in machine modes

## Exit Codes

Standardize them now.

- `0` success
- `1` generic failure
- `2` usage or validation error
- `3` auth failure
- `4` provider/runtime unavailable
- `5` trust or permission refusal
- `6` local data/store unavailable

## Session Model

Syke should not build a persistent REPL yet.

For now:

- each CLI invocation is one operation
- `ask` may be sessionful internally through Pi
- that sessionfulness stays behind the command boundary

This keeps the product simple while preserving runtime capability.

## Visual Tone

The next pass should add design character, but only after the command model is correct.

Desired direction:

- denser and cleaner than standard Python CLIs
- one recognizable status/dashboard layout
- restrained color usage
- memory-system identity instead of coding-assistant identity

This is a presentation layer pass, not a reason to expand the command surface.

## Rollout

### Now

- simplify help and primary docs around the eight core commands
- add machine-readable modes where missing
- add `auth login`
- make trust visible in setup, status, and doctor
- make setup end with an operational summary

### Next

- refine `connect` into a projection-target installer
- add shell completions
- formalize JSON schemas
- add harness-native projection management

### Later

- local RPC or protocol surface for other harnesses
- richer trust management commands
- optional interactive shell if Syke ever needs it

## References

Official sources reviewed for this spec:

- OpenAI Codex auth: <https://developers.openai.com/codex/auth/>
- OpenAI Codex CLI reference: <https://developers.openai.com/codex/cli/reference/>
- OpenAI Codex CLI features: <https://developers.openai.com/codex/cli/features/>
- OpenAI Codex noninteractive: <https://developers.openai.com/codex/noninteractive/>
- OpenAI Codex slash commands: <https://developers.openai.com/codex/cli/slash-commands/>
- Anthropic Claude Code quickstart: <https://docs.anthropic.com/en/docs/claude-code/quickstart>
- Anthropic Claude Code memory: <https://docs.anthropic.com/en/docs/claude-code/memory>
- Gemini CLI authentication: <https://geminicli.com/docs/get-started/authentication/>
- Gemini CLI trusted folders: <https://geminicli.com/docs/cli/trusted-folders/>
- Goose permissions: <https://block.github.io/goose/docs/guides/goose-permissions/>
- Goose ACP clients: <https://block.github.io/goose/docs/guides/acp-clients/>
- Aider docs: <https://aider.chat/docs/>
