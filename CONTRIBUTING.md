# Contributing to Syke

Syke is young — born in a hackathon, growing into infrastructure. Contributions welcome.

## Dev Setup

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
claude login
```

## Tests

```bash
python -m pytest tests/ -v
```

393 tests across 24 files. All external API calls are mocked — no API key needed to run tests.

## Code Style

- Python 3.12+, type hints everywhere
- Pydantic 2.x for data models
- Click for CLI
- Rich for terminal output
- No linter enforced yet — just be consistent with what's there

## Making Changes

1. Fork and branch from `main`
2. Write tests for new functionality
3. Run the full test suite before submitting
4. Keep PRs focused — one feature or fix per PR

## Where to Contribute

| Area | What's Needed |
|------|---------------|
| Adapters | New platform adapters (`syke/ingestion/`) — Twitter, Slack, Notion, etc. |
| Formats | New output formats (`syke/distribution/formatters.py`) |
| CLI | New commands or improvements to `syke/cli.py` |
| Tests | More edge cases, integration tests |
| Docs | Improvements to docs site content |

## Architecture at a Glance

| Layer | Directory | What It Does |
|-------|-----------|-------------|
| Ingestion | `syke/ingestion/` | Platform adapters produce Event objects |
| Storage | `syke/db.py` | SQLite with WAL mode, keyword search |
| Memory | `syke/memory/` | Agent SDK tools for synthesis, memex, and memories |
| Distribution | `syke/distribution/` | Memex distribution, formatters, ask agent |
| CLI | `syke/cli.py` | Click commands wrapping all operations |

## Writing an Adapter

```python
from syke.ingestion.base import BaseAdapter
from syke.models import IngestionResult

class MyAdapter(BaseAdapter):
    source: str = "my-platform"

    def ingest(self, **kwargs) -> IngestionResult:
        # Fetch data, store events in self.db, return IngestionResult
        ...
```

Register it in `cli.py` under the `ingest` group.

## Questions?

Open an issue. No question is too small.
