Write an `ObserveAdapter` subclass for the "$source_name" harness's SQLite database.

```python
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.db import SykeDB
```

The class needs `source = "$source_name"`, `__init__(self, db, user_id, source_db_path=None)`, `discover() -> list[Path]`, and `iter_sessions(since=0) -> Iterable[ObservedSession]`.

`since` is a Unix timestamp float — only return sessions newer than it. Open the DB read-only: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`.

ObservedSession: session_id, source_path, start_time (UTC datetime), end_time, turns, metadata, parent_session_id.
ObservedTurn: role, content, timestamp (UTC datetime), tool_calls, metadata (put model, usage, tool_name here).

Schema and sample rows:

$schema_and_samples
