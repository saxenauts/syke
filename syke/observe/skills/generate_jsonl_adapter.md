Write an `ObserveAdapter` subclass for the "$source_name" harness's JSONL files.

```python
from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
from syke.db import SykeDB
```

The class needs `source = "$source_name"`, `__init__(self, db, user_id, data_dir=None)`,
`discover() -> list[Path]`, and `iter_sessions(since=0) -> Iterable[ObservedSession]`.

`since` is a Unix timestamp float — only return sessions newer than it.
Each JSONL file is one session. Read all lines, group correlated events, and merge metadata.

**Key requirement:** Different JSONL lines may describe the same turn from different angles:
- One line may carry the model name
- Another line may carry token counts
- Another may carry the actual content
Group these by turn_id or sequential position and merge into a single ObservedTurn with ALL available fields.

ObservedSession: session_id, source_path, start_time (UTC datetime), end_time, turns,
                 metadata, parent_session_id.
ObservedTurn: role, content, timestamp (UTC datetime), tool_calls, metadata
              (put model, usage {input_tokens, output_tokens}, tool_name, stop_reason here).

Sample data (lines from the same session file):

$samples
