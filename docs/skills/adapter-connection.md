# Connecting a New Harness to Syke

When someone says "I use X, it's at this path" — follow these steps. Takes 2-5 minutes.

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
from syke.ingestion.parsers import read_jsonl
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
from syke.ingestion.parsers import read_json
data = read_json(path)
# Check: array or dict? where are sessions? where are turns?
```

You need to identify: **what's a session**, **what's a turn**, **where's role/content/timestamp**.

## Step 3: Write the Adapter

One file: `syke/ingestion/{harness}.py`. Every adapter is the same shape:

```python
from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn

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
| JSONL | Claude Code | `syke/ingestion/claude_code.py` |
| JSONL (alt) | Pi | `syke/ingestion/pi.py` |
| SQLite | Hermes | `syke/ingestion/hermes.py` |

## Step 4: Register

Two changes:

1. Update `syke/ingestion/descriptors/{harness}.toml` — set `status = "active"`
2. Add to `syke/ingestion/registry.py` → `get_adapter()`:
```python
if source == "{harness}":
    from syke.ingestion.{harness} import {Harness}Adapter
    return {Harness}Adapter(db, user_id)
```

## Step 5: Test with Real Data

```python
from syke.ingestion.{harness} import {Harness}Adapter
from syke.db import SykeDB

db = SykeDB(':memory:')
adapter = {Harness}Adapter(db, 'test')
result = adapter.ingest()
print(f'{result.events_count} events')  # > 0 means it works
```

## Step 6: Done

Next `syke sync` picks it up automatically through the registry.

## Rules

- No LLM calls in the adapter. Mechanical parsing only.
- No content caps or stripping. Store everything raw.
- If the harness doesn't provide a field, store NULL. Never invent.
- Same input → same events, always. Deterministic.
