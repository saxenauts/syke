Write an `ObserveAdapter` subclass for the "$source_name" harness's SQLite database.

```python
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.db import SykeDB
```

The class needs `source = "$source_name"`, `__init__(self, db, user_id, source_db_path=None)`, `discover() -> list[Path]`, and `iter_sessions(since=0) -> Iterable[ObservedSession]`.

The class needs `iter_sessions(since=0, paths=None) -> Iterable[ObservedSession]`.

`since` is a Unix timestamp float.
`paths` is an optional iterable of explicit database paths.
Contract:
- if `paths` is provided, only inspect those explicit DB files
- if the adapter's configured DB path is not in `paths`, return no sessions
- if `paths` is `None`, use the configured DB path and normal `since` filtering

Open the DB read-only: `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`.

ObservedSession: session_id, source_path, start_time (UTC datetime), end_time, turns, metadata, parent_session_id.
ObservedTurn: role, content, timestamp (UTC datetime), tool_calls, metadata (put model, usage, tool_name here).

Schema and sample rows:

$schema_and_samples
