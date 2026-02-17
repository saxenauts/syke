# Syke — Known Issues & Backlog

Tracked issues that don't affect hackathon submission but should be addressed for production use.

## Bugs

- **Silent exit code 0 on failures** (`cli.py`): Missing API key and no-data-found exit code 0 instead of non-zero. Scripts can't detect failure.
- **Empty user_id allowed** (`config.py`): `SYKE_USER` defaults to `""`, creating `~/.syke/data//` directory.
- **`pydantic-settings` dead dependency** (`pyproject.toml`): Listed but never imported.
- **Sonnet model ID mismatch in pricing** (`llm/client.py`): Pricing table has old Sonnet ID, perceiver uses newer one.
- **Duplicated `daemon/metrics.py`**: Full copy of `syke/metrics.py`. Should import from main.
- **No content size limit on MCP push**: Client can push arbitrarily large events.
- **launchd plist missing `KeepAlive`**: Daemon won't restart on crash.
- **ChatGPT detection gap in setup**: Only checks >100MB ZIPs, misses named `*chatgpt*.zip` patterns.

## Test Coverage Gaps

- CLI (`cli.py`, ~1068 lines): Zero tests
- `run_sync()` orchestration: Zero tests
- Claude Code adapter `ingest()`: Zero tests (only `_make_title` tested)
- GitHub adapter: Zero tests
- LLM client: Zero tests
- Metrics: Zero tests

## Production Readiness

- No `py.typed` marker for type checker support
- No dependency lockfile
- macOS-only daemon (no systemd for Linux)
- No log rotation for `syke.log` and `daemon.log`
- Missing credential filter patterns (Google/Stripe/Azure/JWT keys)
- No MCP authentication (any connected client can read/write)
- `cross_reference` tool is just `search_events` with Python grouping
- Coverage hook source detection uses fragile JSON string matching
