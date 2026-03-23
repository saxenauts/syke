# Connecting a New Harness to Syke

Preferred path on the current branch: use the factory/dynamic adapter flow first. Manual adapter code remains the fallback when that path is not sufficient.

## Step 1: Find the Data

Check the filesystem. Common patterns:
```
~/.{harness}/              # CLI tools (Claude Code, Codex, Pi)
~/Library/Application Support/{harness}/   # macOS apps (Cursor, OpenCode)
~/.local/share/{harness}/  # Linux apps
~/.config/{harness}/       # Config-oriented tools
```

Use `ls`, `find`, or ask the user. You need two things: **where** and **what format**.

## Step 2: Sample the Format

Read a few records to understand the shape.

**JSONL** (most common — Claude Code, Codex, Pi):
```python
from syke.observe.parsers import read_jsonl
lines = read_jsonl(path)
# Check: what line types? what keys per type? where's role, content, timestamp?
```

**SQLite** (Hermes, OpenCode, Cursor):
```python
import sqlite3
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
# Check: what tables? what columns? sample a session + messages.
```

**JSON** (ChatGPT, Gemini CLI, Continue.dev):
```python
from syke.observe.parsers import read_json
data = read_json(path)
# Check: array or dict? where are sessions? where are turns?
```

You need to identify: **what's a session**, **what's a turn**, **where's role/content/timestamp**.

## Step 3: Preferred Path — Factory And Dynamic Adapter

Current default architecture:

- descriptor-driven harness definition
- factory-assisted generate/test/deploy/heal loop
- dynamic adapter loaded from the user's adapters directory

Preferred workflow:

1. identify source path and format cluster
2. write or refine descriptor inputs
3. use `syke.observe.factory` to generate, test, and deploy
4. confirm the generated adapter resolves through the registry
5. run ingest and health checks against real local data

The factory path exists because writing and maintaining every adapter by hand does not scale.

## Step 4: Manual Fallback — Write the Adapter

If the dynamic/factory path is insufficient, write a manual adapter. Every adapter is the same shape:

```python
from syke.observe.observe import ObserveAdapter, ObservedSession, ObservedTurn

class {Harness}Adapter(ObserveAdapter):
    source: str = "{harness}"

    def discover(self) -> list[Path]:
        # Return paths to data files (or DB files) that exist on disk
        # Filter by mtime > last sync

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        # For each discovered file/DB:
        #   Read the data (JSONL lines, SQL rows, JSON structure)
        #   For each session:
        #     For each turn: ObservedTurn(role, content, timestamp, tool_calls)
        #     yield ObservedSession(session_id, turns, metadata, start_time)
```

The base class (ObserveAdapter) handles everything else: session envelopes, turn events, tool events, dedup, transactions, credential sanitization, error recording.

**Reference implementations** (read these for the pattern):

| Format | Reference | File |
|--------|-----------|------|
| Base runtime | ObserveAdapter contract | `syke/observe/observe.py` |
| Structured descriptor path | Structured file adapter | `syke/observe/structured_file.py` |
| Generated path | Dynamic adapter wrapper | `syke/observe/dynamic_adapter.py` |

## Step 5: Register

Two changes:

1. Update `syke/observe/descriptors/{harness}.toml` — set `status = "active"`
2. If this is a true manual builtin path, add the adapter resolution required by the current runtime/registry

## Step 6: Test with Real Data

```python
from syke.observe.{harness} import {Harness}Adapter
from syke.db import SykeDB

db = SykeDB(':memory:')
adapter = {Harness}Adapter(db, 'test')
result = adapter.ingest()
print(f'{result.events_count} events')  # > 0 means it works
```

## Step 7: Done

Next `syke sync` should pick it up through the current runtime path.

## Rules

- No LLM calls in the adapter. Mechanical parsing only.
- No content caps or stripping. Store everything raw.
- If the harness doesn't provide a field, store NULL. Never invent.
- Same input → same events, always. Deterministic.

If both paths are possible, prefer:

1. factory + dynamic adapter
2. manual adapter fallback
