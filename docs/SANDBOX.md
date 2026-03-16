# Syke Observe Sandbox

Validation and testing framework for the Observe/Sense layer. Two modes, one goal: prove every Observation Principle holds against real data.

## Two Modes

**Zero Setup (CI/deterministic):** Isolated DB, synthetic fixtures, no external dependencies. Every proof target generates its own test data via helpers. Runs in 1.4 seconds.
```bash
.venv/bin/pytest tests/sandbox/ -v
```

**Continue (real data):** Validates against your actual machine data — OpenCode sessions, Claude Code JSONL, Codex archives. Requires live harness data on disk. Uses SYKE_DB env override for sandbox isolation.
```bash
SYKE_DB=/tmp/sandbox.db .venv/bin/pytest tests/sandbox/ -v -m real_data
```

## Observation Principles (O1–O6)

These are the validation criteria. Every proof target maps to at least one principle.

| # | Principle | Constraint | Validation Query |
|---|-----------|-----------|-----------------|
| O1 | Time Is The Axis | Every event has a timestamp. Monotonic within sessions. | `SELECT COUNT(*) FROM events WHERE timestamp IS NULL` → 0 |
| O2 | Sessions Are Information Units | Never infer boundaries. Never merge across sources. Concurrent sessions = distinct IDs. | `SELECT COUNT(DISTINCT session_id)` per source |
| O3 | Two Axes | turn + tool_call + tool_result + session.start + ingest.error. Nothing else from observe adapters. | `SELECT event_type, COUNT(*)` outside canonical set → 0 rows |
| O4 | Real-Time | Events in ledger within seconds of harness write. File watcher primary, daemon is safety net. | Write JSONL line → event in DB within 5s |
| O5 | Self-Observation | source='syke' events for synthesis, ingestion, healing. Same ledger, same timeline. | `SELECT * FROM events WHERE source='syke'` returns rows |
| O6 | Healing Is Core | Broken adapter detected → healed → events resume. No failure goes undetected. | Format change → failure → healing.triggered → recovery |

## Proof Targets

| Proof | File | Principle | What It Proves |
|-------|------|-----------|---------------|
| 5.1 | test_single_session.py | O1, O3 | Single harness, single session: correct event count, types, roles, sequence, required fields, idempotent reingest |
| 5.2 | test_concurrent_sessions.py | O2 | 3 concurrent sessions: distinct session_ids, distinct source_instance_ids, no collisions, isolation |
| 5.3 | test_subagent_hive.py | O2 | Parent-child session tree: 4 distinct sessions, parent links, root has no parent, children have events |
| 5.4 | test_multi_harness_timeline.py | O1, O3 | 3+ harnesses: events interleaved by timestamp, both axes present, no cross-source session leak |
| 5.5 | test_realtime_latency.py | O4 | File write → DB event: p99 < 5 seconds, 100% capture rate |
| 5.6 | test_self_observation.py | O5 | source='syke' events created, no recursive observation, correct timeline |
| 5.7 | test_idempotency.py | P6 | 5x reingest = 0 new events. Cross-adapter = no collision. external_id dedup works |
| 5.8 | test_crash_recovery.py | P5, P6 | Partial ingest recovery, truncated JSON handled, zero duplicates after double ingest |
| 5.9 | test_healing_flow.py | O6 | Format change → health drops → sustained low triggers callback → success resets → full heal cycle |

## Test Fixtures

`tests/sandbox/conftest.py` provides:
- `sandbox_db` — isolated SQLite database (cleaned per test)
- `sandbox_dir` — temp directory mimicking harness file structure
- `claude_adapter` — ClaudeCodeAdapter pointed at sandbox
- `codex_adapter` — configured for sandbox

`tests/sandbox/helpers.py` provides data builders:
- `write_claude_code_session(dir, turns, tools)` — generates realistic JSONL
- `write_codex_session(dir, turns)` — generates Codex JSONL
- `write_opencode_db(path, sessions, messages)` — generates OpenCode SQLite
- `count_events(db, source)` — query helper
- `query_events(db, **filters)` — flexible event query

## Sense Module Map

```
syke/sense/
├── tailer.py       85 LOC   JsonlTailer — inode-tracked offset reading
├── writer.py      148 LOC   SenseWriter — single-writer thread, 50ms/100-batch
├── handler.py     103 LOC   SenseFileHandler — routes file events to tailer
├── watcher.py      83 LOC   SenseWatcher — watchdog (FSEvents/inotify)
├── sqlite_watcher  172 LOC   SQLiteWatcher — 1s mtime polling, 3-tier backoff
├── healing.py     216 LOC   HealingLoop — 5-axis health score, threshold trigger
├── self_observe.py 115 LOC   SykeObserver — 15 event types, external_id gen
├── discovery.py    70 LOC   SenseDiscovery — filesystem scan for 10+ harnesses
├── analyzer.py     98 LOC   SenseAnalyzer — format inference, field detection
├── sandbox.py     108 LOC   AdapterSandbox — AST safety + subprocess isolation
├── adapter_gen.py 177 LOC   AdapterGenerator — LLM-powered, 3 retries
├── intelligence.py 159 LOC   SenseIntelligence — discover→analyze→generate→test→deploy
├── dynamic_adapter 170 LOC   DynamicAdapter — wraps parse_line(), canonical events
└── registry.py    141 LOC   Dynamic registry — @register_adapter decorator
```

## Configuration Defaults

| Component | Setting | Default |
|-----------|---------|---------|
| SenseWriter | flush interval | 50ms |
| SenseWriter | max batch size | 100 events |
| SQLiteWatcher | poll interval | 1 second |
| SQLiteWatcher | retry backoffs | 0.1s, 0.5s, 2.0s |
| HealingLoop | health threshold | 0.3 |
| HealingLoop | sustained minutes | 2.0 |
| HealingLoop | max samples | 20 |
| AdapterSandbox | timeout | 30 seconds |
| Health Score | weights | completeness 0.30, granularity 0.20, error_rate 0.25, drift 0.10, freshness 0.15 |

## Running Validations

Full sandbox (deterministic):
```bash
.venv/bin/pytest tests/sandbox/ -v                    # 33 tests, ~1.4s
```

Observe core:
```bash
.venv/bin/pytest tests/test_observe.py -v             # 27 tests, ~8s
```

Sense infrastructure:
```bash
.venv/bin/pytest tests/test_sense_*.py -v             # 15 tests, ~3s
```

Healing:
```bash
.venv/bin/pytest tests/test_healing_*.py -v           # healing loop + wiring
```

Full suite:
```bash
.venv/bin/pytest tests/ -q                            # 546 tests, ~20s
```

Live DB validation (O1-O6 against production data):
```sql
-- O1: no null timestamps
SELECT COUNT(*) FROM events WHERE timestamp IS NULL;

-- O2: session coverage
SELECT source, COUNT(DISTINCT session_id) FROM events WHERE session_id IS NOT NULL GROUP BY source;

-- O3: type purity (should return 0 rows)
SELECT event_type, COUNT(*) FROM events
WHERE source NOT IN ('github','gmail','chatgpt','syke','manual','manual-debug')
AND event_type NOT IN ('turn','tool_call','tool_result','session.start','session','ingest.error','observation')
GROUP BY event_type;

-- O4: latency check
SELECT source, ROUND(AVG((julianday(ingested_at) - julianday(timestamp))*86400), 1) as avg_latency_s
FROM events WHERE ingested_at IS NOT NULL AND source IN ('opencode','claude-code')
GROUP BY source;

-- O5: self-observation
SELECT event_type, COUNT(*) FROM events WHERE source='syke' GROUP BY event_type;

-- Tool correlation integrity
SELECT COUNT(*) as tool_calls, SUM(CASE WHEN tool_correlation_id IS NOT NULL THEN 1 ELSE 0 END) as with_id
FROM events WHERE event_type='tool_call';
```

## Real Data Benchmarks (this machine, March 16 2026)

| Source | Events | Sessions | Adapte | Legacy % | External ID Coverage |
|--------|--------|----------|--------|----------|---------------------|
| OpenCode | 120,319 | 3,891 | ObserveAdapter | 0% | 99.99% (3 nulls) |
| Claude Code | 6,392 | 28 | Mixed | 95% legacy | 5% coverage |
| Codex | 141 | 0 | Legacy only | 100% | 0% |
| GitHub | 1,425 | — | Flat events | N/A | N/A |
| Syke | 68 | — | Self-observe | N/A | N/A |
| Manual | ~200 | — | CLI records | N/A | N/A |
