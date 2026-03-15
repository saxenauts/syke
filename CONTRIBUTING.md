# Contributing to Syke

Syke is young — born in a hackathon, growing into infrastructure. Contributions welcome.

## Dev Setup

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# Set up a provider (choose one):
claude login                    # Claude Code
syke auth use codex             # ChatGPT Plus
syke auth set openrouter --api-key YOUR_KEY  # OpenRouter
syke auth set kimi --api-key YOUR_KEY        # Kimi
```

## Tests

```bash
python -m pytest tests/ -v
```

293 tests across 24 files. All external API calls are mocked — no API key needed to run tests.

## Code Style

- Python 3.12+, type hints everywhere
- Pydantic 2.x for data models
- Click for CLI
- Rich for terminal output
- Ruff for linting and formatting — enforced in CI (`ruff check` + `ruff format --check`). Config in `pyproject.toml` under `[tool.ruff]`

## Making Changes

1. Fork and branch from `main`
2. Write tests for new functionality
3. Run the full test suite before submitting
4. Keep PRs focused — one feature or fix per PR

## Where to Contribute

| Area | What's Needed |
|------|---------------|
| Adapters | New platform adapters (`syke/ingestion/`) — Twitter, Slack, Notion, etc. |
| CLI | New commands or improvements to `syke/cli.py` |
| Tests | More edge cases, integration tests |
| Docs | Improvements to docs site content |

## Architecture at a Glance

| Layer | Directory | What It Does |
|-------|-----------|-------------|
| Ingestion | `syke/ingestion/` | Platform adapters produce Event objects |
| Storage | `syke/db.py` | SQLite with WAL mode, keyword search |
| Memory | `syke/memory/` | Agent SDK tools for synthesis, memex, and memories |
| Distribution | `syke/distribution/` | Memex distribution, context files, ask agent |
| CLI | `syke/cli.py` | Click commands wrapping all operations |

## Writing an Adapter

See `docs/skills/adapter-connection.md` for the 6-step process.

```python
from pathlib import Path
from collections.abc import Iterable

from syke.ingestion.observe import ObserveAdapter, ObservedSession, ObservedTurn

class MyAdapter(ObserveAdapter):
    source: str = "my-platform"

    def discover(self) -> list[Path]:
        # Find data files on disk
        ...

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        # Parse files into sessions with turns
        ...
```

Register it in `syke/ingestion/registry.py` under `get_adapter()`.

## Questions?

Open an issue. No question is too small.
