# Contributing to Syke

Syke is under active development on the 0.5 branch. Contributions should stay aligned with the current memex-first, observe-first architecture.

## Dev Setup

```bash
git clone https://github.com/saxenauts/syke.git && cd syke
uv sync --extra dev --locked
# Set up a provider (choose one):
codex login
uv run syke auth use codex      # ChatGPT Plus
uv run syke auth set openrouter --api-key YOUR_KEY --use  # OpenRouter
uv run syke auth set openai --api-key YOUR_KEY --model gpt-5-mini --use
uv run syke auth status
```

## Tests

```bash
uv run pytest tests -v --tb=short
```

This is the canonical local suite and the same suite CI runs.

- Use `uv run pytest`, not `python -m pytest`, so the repo's locked interpreter and deps match CI.
- The default suite must not touch real user state under `~/.syke` or `~/.config/syke`.
- `tests/test_pi_integration.py` is opt-in and requires `SYKE_RUN_PI_INTEGRATION=1`.
- Run targeted subsets when iterating locally, but do not treat them as a substitute for the managed suite before submission.

See [docs/TESTING.md](docs/TESTING.md) for the full lane breakdown and isolation contract.

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
| Adapters | Observe descriptors, factory flow, and adapter/runtime improvements |
| CLI | New commands in `syke/cli_commands/`, shared CLI logic in `syke/cli_support/`, root composition in `syke/entrypoint.py` |
| Tests | More edge cases, integration tests |
| Docs | Keep the live docs aligned with the current branch reality |

## Architecture at a Glance

| Layer | Directory | What It Does |
|-------|-----------|-------------|
| Observe | `syke/observe/` | Deterministic capture into the immutable timeline |
| Storage | `syke/db.py` | SQLite timeline, memex storage, cycle records |
| Memory | `syke/memory/` | Synthesis loop and memex handling |
| Distribution | `syke/distribution/` | Memex projections, harness adapters, runtime-facing context |
| Runtime | `syke/runtime/`, `syke/llm/` | Pi workspace, provider resolution, runtime wiring |
| CLI | `syke/entrypoint.py`, `syke/cli_commands/`, `syke/cli_support/` | Root Click composition, command families, and shared CLI support |

## Writing an Adapter

Follow the current factory-first flow in `syke.observe.factory` and the adapter/runtime patterns already present in `syke/observe/`.

```python
from pathlib import Path
from collections.abc import Iterable

from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn

class MyAdapter(ObserveAdapter):
    source: str = "my-platform"

    def discover(self) -> list[Path]:
        # Find data files on disk
        ...

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        # Parse files into sessions with turns
        ...
```

If you need a true manual adapter path, integrate it with the current runtime/registry flow rather than assuming an older static registration pattern.

## Questions?

Open an issue. No question is too small.
