Write a Python class that extends `ObserveAdapter` to read sessions from the "$source_name" AI harness's SQLite database.

## What this class does

It reads sessions and turns from the harness's SQLite database and yields `ObservedSession` objects. Each session contains `ObservedTurn` objects representing individual messages.

## Required imports and base classes

```python
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.db import SykeDB
```

## Required class structure

The adapter takes an optional `source_db_path` keyword argument for the harness database location. If not provided, it should have a sensible default path.

```python
class MyAdapter(ObserveAdapter):
    source = "$source_name"

    def __init__(self, db: SykeDB, user_id: str, source_db_path: Path | None = None):
        super().__init__(db, user_id)
        self.source_db_path = source_db_path or Path("~/.hermes/state.db").expanduser()

    def discover(self) -> list[Path]:
        return [self.source_db_path] if self.source_db_path.exists() else []

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        # Open self.source_db_path read-only, query sessions and their messages
        # Yield ObservedSession objects with ObservedTurn lists
        ...
```

## ObservedSession fields

```python
ObservedSession(
    session_id=str,          # REQUIRED: unique session identifier
    source_path=Path,        # path to the database file
    start_time=datetime,     # session start timestamp (UTC)
    end_time=datetime|None,  # session end timestamp
    project=str|None,        # project/workspace name
    parent_session_id=str|None,
    turns=[ObservedTurn(...)],
    metadata={},             # extra session-level data
)
```

## ObservedTurn fields

```python
ObservedTurn(
    role=str,                # "user" | "assistant" | "system"
    content=str,             # the message text
    timestamp=datetime,      # message timestamp (UTC)
    tool_calls=[],           # list of tool call dicts with block_type, tool_name, etc.
    metadata={               # per-turn metadata
        "model": str|None,
        "stop_reason": str|None,
        "usage": {"input_tokens": int, "output_tokens": int},
        "tool_name": str|None,
    },
)
```

## Database schema and sample data

$schema_and_samples

## Quality contract

Your adapter will be tested against the database above. To pass:

- iter_sessions() must yield ObservedSession objects with session_id and start_time
- Each session must contain ObservedTurn objects with role, content, and timestamp
- model and token counts should be extracted from the database when available
- tool_calls should be populated when the data has tool usage
- Open the database read-only: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- Handle missing/null data gracefully — never raise on partial rows
- Convert timestamps to UTC datetime objects (handle both Unix seconds and milliseconds)

## Rules

- Import only `sqlite3`, `json`, `datetime`, `pathlib`, and `collections.abc` from stdlib
- Import `ObserveAdapter`, `ObservedSession`, `ObservedTurn` from `syke.observe.adapter`
- Import `SykeDB` from `syke.db`
- **The schema and sample data above are the ground truth.** Match the actual column names and structure.
- The `since` parameter is a Unix timestamp float. Use it to filter sessions: only return sessions updated after `since`.
