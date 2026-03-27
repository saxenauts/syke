# Runtime and Replay Guide

## What This Guide Is For

This is the operator view of Syke's current runtime shape.

It covers:

- how provider selection works
- how `ask`, `sync`, and the daemon route work through Pi
- what the Pi workspace contains
- how replay experiments map onto the same runtime

For the broader system model, read [ARCHITECTURE.md](ARCHITECTURE.md).

## The Current Mental Model

There are still two separate concepts:

- **Provider**: which model service Pi should use, for example `codex`, `openai`, `openrouter`, `azure`, `zai`, or `kimi`
- **Runtime**: how the agent executes work

Syke's runtime is now Pi only.

`syke.llm.runtime_switch` remains the stable import surface, but it always returns `"pi"` and always dispatches to the Pi backend implementations.

## Responsibility Split

Pi is now responsible for the agent runtime itself:

- executing the agent loop
- maintaining runtime session state and session artifacts
- handling model/provider execution once Syke has prepared the workspace and provider config
- streaming runtime events, tool execution events, and final responses back to Syke
- exposing runtime-native controls such as compaction, retry, session stats, and export surfaces

Syke is responsible for the memory product around that runtime:

- observing external systems and writing the append-only event ledger
- defining the DB schema, user-owned SQLite store, and replay/eval surfaces
- deciding what the workspace means: `events.db`, `agent.db`, `memex.md`, sandbox policy, and helper scripts
- deciding what synthesis should do and how ask/sync/daemon flows are grounded in local memory
- recording product-level metrics, self-observation events, and distributing the memex back out to harnesses

The boundary is intentional: Pi runs the agent, while Syke decides what memory exists, what the agent is allowed to see, and how the result feeds back into the product.

## Runtime Routing Today

`ask` and synthesis always route to:

- `syke.llm.backends.pi_ask.pi_ask`
- `syke.llm.backends.pi_synthesis.pi_synthesize`

The `[runtime]` config section is kept only so older configs do not break. Keep:

```toml
[runtime]
backend = "pi"
```

Legacy `runtime = "..."` is still accepted by the config loader, but new configs should use `[runtime].backend = "pi"`.

## Provider Routing Today

Provider resolution is:

1. CLI `--provider`
2. `SYKE_PROVIDER`
3. `~/.syke/auth.json` active provider

Syke then translates provider config into:

- Pi environment variables
- workspace-local `.pi/settings.json`
- optional provider extension files under `.pi/extensions/`

Examples:

- `azure` becomes Pi's `azure-openai-responses` provider
- `openrouter` maps directly to Pi's built-in `openrouter`
- `vllm` and `llama-cpp` are exposed through generated OpenAI-compatible Pi extensions

## What `ask`, `sync`, and the Daemon Do

### `syke ask`

`syke ask` now tries the local daemon first over a Unix domain socket. If the daemon is running, ask is served inside the daemon process against its already-warm Pi runtime. If the socket is unavailable or the IPC path fails, Syke falls back to the existing in-process Pi path.

In both cases, ask refreshes the Pi workspace from the current DB and syncs the current memex into `memex.md` before Pi runs.

The important detail is that it rebuilds the workspace from the exact `SykeDB` instance it was called with, not from a default user DB path. If a test, replay, or temporary run opens `/tmp/replay.db`, `ask` now snapshots that exact DB into workspace `events.db` before Pi runs.

That fixes the stale-workspace failure mode where a call against an empty or alternate DB could still inherit evidence from an older real-user workspace snapshot.

The ask backend returns:

- final answer text
- duration
- provider/model metadata
- input/output token counts
- cache read/write counts
- cost
- tool-call count

Pi ask metrics also now record runtime-level details into `metrics.jsonl`, including:

- whether the runtime was warm-reused or cold-started
- whether ask ran through daemon IPC or direct fallback
- daemon IPC fallback/error details when the socket path could not be used
- Pi process PID, uptime, start cost, and session count
- response ID and stop reason
- tool name counts
- workspace snapshot refresh/skip result and refresh duration

Ask self-observation now also emits:

- `ask.start`
- `ask.complete`
- `ask.tool_use`

If there is no data yet, it returns a grounded no-data message without spinning up Pi.

### `syke sync`

`syke sync`:

1. ingests new source events
2. refreshes the workspace snapshot
3. runs Pi synthesis
4. validates workspace outputs
5. syncs `memex.md` back into the main Syke DB
6. distributes the memex to harness adapters

### Daemon

The daemon follows the same flow as `syke sync`, but keeps the Pi runtime warm and reuses it across cycles.

The daemon starts the Pi runtime up front because Pi is the canonical runtime, not an alternate execution path.

Background registration now targets Syke's stable launcher at `~/.syke/bin/syke` instead of binding launchd/cron directly to whichever install surface happened to run setup.

That matters because:

- daemon registrations now survive package-manager path drift better
- `syke daemon status` can report one stable launcher path
- Syke can swap the launcher target during reinstall/update without rewriting every background registration

Current limitation:

- on macOS, source-dev installs inside TCC-protected directories such as `~/Documents` still are not safe launchd targets; the daemon will now only register a safe non-editable installed `syke` whose install provenance matches the current checkout, and otherwise the LaunchAgent install fails with instructions to reinstall the checkout or move it instead of silently pointing at some other binary

## Runtime Telemetry Today

Syke now captures Pi runtime telemetry in three places:

- `metrics.jsonl` for ask/synthesis operation records
- `source='syke'` self-observation rows for synthesis lifecycle and per-tool traces
- `syke observe` runtime summaries for operator-facing health

Current runtime telemetry includes:

- provider/model, response ID, stop reason
- input/output/cache read/cache write tokens
- tool-call counts and per-tool name counts
- warm-reuse vs cold-start signals
- daemon-served ask vs direct ask counts, plus IPC fallback counts
- Pi PID, uptime, start duration, session count
- workspace snapshot refresh duration and whether the snapshot was skipped because the source DB was unchanged

This matters because Pi is being treated as a real runtime now, not just a stateless RPC shim.
The right eval surface is not only cost and final text quality, but also:

- whether Syke is keeping the runtime warm
- whether workspace refreshes are being avoided when no evidence changed
- how many tool calls the agent needed
- how much cache reuse the provider/runtime is getting

## The Pi Workspace Contract

The workspace lives at `~/.syke/workspace/`.

Important artifacts:

- `events.db`: readonly snapshot of the main event timeline
- `agent.db`: writable runtime-owned working store
- `memex.md`: current synthesized memex
- `sessions/`: Pi session JSONL audit trail
- `scripts/`: persistent helper scripts Pi can build and reuse

This workspace is the stable contract between Syke and the agent runtime.

## Pi Capabilities To Exploit Next

Pi already has more runtime surface area than Syke is using today. The most useful next steps are:

- persistent session lineage across `ask`, synthesis, daemon cycles, and replay runs
- richer runtime metrics from Pi session stats instead of only per-call token extraction
- runtime-native compaction and retry policy as first-class operational controls
- extension hooks for memory-specific commands, tools, provider adapters, and guardrails
- exportable session artifacts for audit, debugging, and replay comparison
- session-based prompt caching and transport selection where the underlying provider supports it

The core migration is that Pi is no longer being treated as a stateless JSON-RPC wrapper. RPC is just the control plane into a long-lived runtime with workspace, sessions, and runtime-native observability.

## Replay Experiments

`experiments/memory_replay.py` replays a frozen event dataset day by day through the same Pi synthesis path used by production.

High-level flow:

1. create a fresh replay DB
2. copy one day of events into it
3. run Pi synthesis
4. snapshot the memex and metrics
5. repeat for the next day

Useful commands:

### Dry run

```bash
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir experiments/runs/local_dry_run \
  --user-id replay_dry \
  --source-user-id fresh_test \
  --dry-run
```

### Short run

```bash
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir experiments/runs/local_pi_3d \
  --user-id replay_pi_3d \
  --source-user-id fresh_test \
  --model qwen3-coder \
  --max-days 3 \
  --skill experiments/prompts/minimal.md
```

### Start from a later window

```bash
python experiments/memory_replay.py \
  --source-db experiments/data/frozen_saxenauts.db \
  --output-dir experiments/runs/local_feb_window \
  --user-id replay_feb_window \
  --source-user-id fresh_test \
  --start-day 2026-02-01 \
  --max-days 5
```

## Notes

- `--runtime` in older replay commands is now legacy. Pi is the only supported runtime.
- `--model` is the useful runtime override for replay work now.
- If you are comparing replay runs, compare provider/model/prompt condition changes, not Claude-vs-Pi backend choice, because that backend split is gone.
