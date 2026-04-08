# Runtime Guide

## What This Guide Is For

This is the operator view of Syke's current runtime shape.

It covers:

- how provider selection works
- how `ask`, `sync`, and the daemon route work through Pi
- what the Pi workspace contains

For the broader system model, read [ARCHITECTURE.md](ARCHITECTURE.md).

## The Current Mental Model

There are two separate concepts:

- **Provider**: which model service Pi should use, for example `codex`, `openai`, `openrouter`, `azure`, `zai`, or `kimi`
- **Runtime**: how the agent executes work

Syke's runtime is Pi only.
`syke.llm.pi_runtime` is the Pi-native routing surface and dispatches directly to Pi backends.

## Responsibility Split

Pi is now responsible for the agent runtime itself:

- executing the agent loop
- maintaining runtime session state and session artifacts
- handling model/provider execution once Syke has prepared the workspace and provider config
- streaming runtime events, tool execution events, and final responses back to Syke
- exposing runtime-native controls such as compaction, retry, session stats, and export surfaces

Syke is responsible for the memory product around that runtime:

- installing adapter markdowns so the agent can read harness data directly
- defining the DB schema, user-owned SQLite store, and replay/eval surfaces
- deciding what the workspace means: `syke.db`, `MEMEX.md`, adapter markdowns
- deciding what synthesis should do and how ask/sync/daemon flows are grounded in local memory
- recording product-level metrics, self-observation events, and distributing the memex back out to harnesses

The boundary is intentional: Pi runs the agent, while Syke decides what memory exists, what the agent is allowed to see, and how the result feeds back into the product.

Adapter markdown installation happens at workspace initialization. `initialize_workspace()` is called once by the daemon at startup (or by setup). Seed adapter markdowns are shipped in `syke/observe/seeds/` and installed to `~/.syke/adapters/{source}.md`. The agent reads harness data directly during synthesis and ask via bash/sqlite3.

## Runtime Routing Today

`ask` and synthesis always route to:

- `syke.llm.backends.pi_ask.pi_ask`
- `syke.llm.backends.pi_synthesis.pi_synthesize`

## Provider Routing Today

Provider resolution is:

1. CLI `--provider`
2. `SYKE_PROVIDER`
3. `~/.syke/pi-agent/settings.json` `defaultProvider`

Syke then resolves runtime state from Pi-native files under `~/.syke/pi-agent/`:

- `auth.json` for credentials
- `settings.json` for active provider and model
- `models.json` for endpoint or base-url overrides

Examples:

- `openrouter` maps directly to Pi's built-in `openrouter`
- `azure-openai-responses` requires a configured endpoint or base URL in Pi state

## What `ask`, `sync`, and the Daemon Do

### `syke ask`

`syke ask` now tries the local daemon first over a Unix domain socket. If the daemon is running, ask is served inside the daemon process against its already-warm Pi runtime. If the socket is unavailable or the IPC path fails, Syke falls back to the existing in-process Pi path.

The IPC protocol is versioned (`IPC_PROTOCOL_VERSION = 1`) and supports two message types:

- **`ask`**: Routes a question through the daemon's warm runtime. The daemon rejects overlapping asks with `DaemonIpcBusy` if the runtime is already serving another request (synthesis or ask). The client falls back to direct in-process Pi rather than queuing.
- **`runtime_status`**: Returns the daemon's current warm runtime state — whether it is alive, busy, which provider/model is bound, daemon PID, uptime, and any binding errors. Used by `syke status` and `syke daemon status` for runtime introspection.

Ask timeout handling includes a 5-second buffer beyond the configured ask timeout to account for daemon processing overhead. If the daemon's IPC socket disappears (e.g., after a crash), the daemon auto-recovers by rebinding the socket on the next cycle.

In both cases, ask binds workspace `syke.db` and ensures adapter markdowns are installed before Pi runs. The agent reads harness data directly via adapter guides + bash/sqlite3.

The ask backend returns:

- final answer text
- duration
- provider/model metadata
- input/output token counts
- cache read/write counts
- cost
- assistant turn count
- tool-call count

Pi ask metrics also now record runtime-level details (rollout traces in `syke.db`), including:

- whether the runtime was warm-reused or cold-started
- whether ask ran through daemon IPC or direct fallback
- daemon IPC fallback/error details when the socket path could not be used
- Pi process PID, uptime, start cost, and session count
- response ID and stop reason
- tool name counts

Ask self-observation now also emits:

- `ask.start`
- `ask.complete`
- `ask.tool_use`

If there is no data yet, it returns a grounded no-data message without spinning up Pi.

### `syke sync`

`syke sync` runs one synthesis cycle directly (or starts the daemon with `--start-daemon-after`):

1. runs Pi synthesis (the agent always runs; it receives temporal context and decides via adapter guides whether anything warrants updating)
2. syncs `MEMEX.md` back into the main Syke DB
3. advances the synthesis cursor
4. refreshes the exported memex and registered Syke capability files

There is no threshold gate. Synthesis always runs. The agent decides via temporal context and adapter data whether to update the MEMEX.

### Daemon

The daemon follows the same flow as `syke sync`.

On the macOS launchd path, it also keeps the Pi runtime warm and reuses it across cycles. Other install surfaces currently use periodic `syke sync` invocations instead of one warm long-lived process.

The persistent daemon path starts the Pi runtime up front because Pi is the canonical runtime, not an alternate execution path.

#### Daemon Locking

The daemon uses an exclusive `fcntl.flock()` file lock at `~/.config/syke/daemon.lock` to prevent duplicate instances. If a second `syke daemon start` is attempted while one is running, it fails immediately and cleanly instead of creating competing processes.

#### Daemon Logging

All daemon logging uses a symmetric tag-based format via `DaemonFormatter`:

```
2026-04-03 00:52:08 SYNC   new events ingested
2026-04-03 00:52:12 SYNTH  synthesis complete
2026-04-03 00:52:13 DIST   memex exported
```

Tags are mapped from module names: `SYNC`, `OBS`, `PI`, `SYNTH`, `ASK`, `COST`, `DIST`, `MEM`, `CONF`, `IPC`, `LOG`. This format applies uniformly to `daemon.log` and structured log consumers.

#### Adaptive Retry

If a daemon cycle fails (sync error, synthesis failure, etc.), the daemon retries after `min(interval, 5)` seconds instead of waiting the full interval. On success, it waits the full configured interval. Failed syntheses do not trigger distribution — only successful synthesis results are distributed downstream.

Background registration now targets Syke's stable launcher at `~/.syke/bin/syke` instead of binding launchd/cron directly to whichever install surface happened to run setup.

That matters because:

- daemon registrations now survive package-manager path drift better
- `syke daemon status` can report one stable launcher path
- Syke can swap the launcher target during reinstall/update without rewriting every background registration

Current limitation:

- on macOS, source-dev installs inside TCC-protected directories such as `~/Documents` still are not safe launchd targets; the daemon will now only register a safe non-editable installed `syke` whose install provenance matches the current checkout, and otherwise the LaunchAgent install fails with instructions to reinstall the checkout or move it instead of silently pointing at some other binary
- immediately after install/start on macOS, the daemon may still be warming Pi and binding the IPC socket; setup and daemon commands now treat that state as startup/warm-up rather than a hard failure

### Adapter Markdown Installation

There are no file watchers. The agent reads harness data directly.

`initialize_workspace()` is called once by the daemon at startup. It calls `ensure_adapters()` which installs adapter markdowns from shipped seeds in `syke/observe/seeds/` to `~/.syke/adapters/{source}.md`. Flat files, no per-adapter subdirectories.

## Runtime Telemetry Today

Syke now captures Pi runtime telemetry in two places:

- rollout traces in `syke.db` for ask/synthesis operation records
- `source='syke'` self-observation rows for synthesis lifecycle and per-tool traces

Current runtime telemetry includes:

- provider/model, response ID, stop reason
- input/output/cache read/cache write tokens
- assistant turn counts
- tool-call counts and per-tool name counts
- warm-reuse vs cold-start signals
- daemon-served ask vs direct ask counts, plus IPC fallback counts
- Pi PID, uptime, start duration, session count
- synthesis cycle number and outcome

This matters because Pi is being treated as a real runtime now, not just a stateless RPC shim.
The right eval surface is not only cost and final text quality, but also:

- whether Syke is keeping the runtime warm
- how many tool calls the agent needed
- how much cache reuse the provider/runtime is getting
- how the agent uses adapter markdowns to navigate harness data

## The Pi Workspace Contract

The workspace lives at `~/.syke/`.

Important artifacts:

- `syke.db`: writable learned-memory store (real file at `~/.syke/syke.db`, not a symlink)
- `MEMEX.md`: current routed memex artifact for the workspace, indexed by synthesis cycle numbers (4,000 token budget with fill indicator and hard gate)
- `PSYCHE.md`: agent identity contract, written by `initialize_workspace()`
- `adapters/{source}.md`: per-harness adapter markdowns
- `sessions/`: Pi session JSONL audit trail

The semantic contract is:

- `syke.db` = mutable learned memory surface (memories, links, memory_ops, synthesis_cursor, cycle_records, cycle_annotations, memories_fts, rollout traces)
- `MEMEX.md` = routed artifact written inside the workspace and synced back into the store (4,000 token budget, agent sees fill % in header)
- `PSYCHE.md` = agent identity — establishes who the agent is and its behavioral contract
- adapter markdowns = how the agent finds and reads harness data via bash/sqlite3

## Sandbox Model

The current model is:

- OS-level sandbox with deny-default reads, catalog-scoped per-user profile
- the agent has read access to catalog-known harness data directories + system paths, and read/write access to `~/.syke/`
- everything else (`~/Documents`, `~/.ssh`, `~/.gnupg`, etc.) is denied by default
- external harness sandboxes outside Syke's control

Inside Syke, the OS sandbox is the meaningful runtime boundary. On macOS, this is a seatbelt profile generated at launch time from the harness catalog. It combines:

- deny-default filesystem reads with per-user catalog-scoped whitelisting
- write access restricted to `~/.syke/` workspace + temp dirs
- network outbound allowed (API calls)
- explicit denial of credential paths

## Pi Capabilities To Exploit Next

Pi already has more runtime surface area than Syke is using today. The most useful next steps are:

- persistent session lineage across `ask`, synthesis, daemon cycles, and replay runs
- richer runtime metrics from Pi session stats instead of only per-call token extraction
- runtime-native compaction and retry policy as first-class operational controls
- extension hooks for memory-specific commands, tools, provider adapters, and guardrails
- exportable session artifacts for audit, debugging, and replay comparison
- session-based prompt caching and transport selection where the underlying provider supports it

The core migration is that Pi is no longer being treated as a stateless JSON-RPC wrapper. RPC is just the control plane into a long-lived runtime with workspace, sessions, and runtime-native observability.

## Internal Eval Tooling

Replay and prompt-eval tooling are intentionally not part of the tracked OSS repo surface.

If you run private or local evaluation workflows, treat them as internal operator tooling rather than part of the product contract documented here.

## Notes

- Pi is the only supported runtime.
- Local eval workflows should not redefine the runtime contract described in this document.
