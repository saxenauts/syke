# Testing Guide

This is the contributor test contract for Syke.

## Canonical Local Command

Run this before opening a PR or trusting a local pass:

```bash
uv run pytest tests -v --tb=short
```

This is the same managed suite CI runs.

Use `uv run pytest`, not `python -m pytest`, so local runs use the repo-managed interpreter and locked dependencies.

## Isolation Contract

The default suite must not read from or write to a developer's real Syke state.

- `tests/conftest.py` redirects `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_CACHE_HOME` to a synthetic test home before Syke modules import.
- Each test also gets isolated Pi agent, workspace, and data paths under `tmp_path`.
- New tests must preserve that contract. Do not hardcode real `~/.syke`, `~/.config/syke`, or live provider state into test setup.

If a test needs explicit path behavior, point it at `tmp_path` or patch module bindings directly.

## Test Lanes

### 1. Managed suite

This is the default contributor gate and CI lane.

- Covers the tracked unit and integration tests in `tests/`.
- If this passes, "all tests pass" means the managed suite actually passed.
- Command: use the canonical local command above.

### 2. Pi integration lane

Run this only when changing Pi runtime wiring, provider probing, or auth/runtime handshake behavior.

```bash
SYKE_RUN_PI_INTEGRATION=1 uv run pytest tests/test_pi_integration.py -v
```

Notes:

- Requires a working local Pi binary and valid provider auth.
- This is opt-in and not part of the default CI gate.

### 3. Focused runtime lane

These tests exercise thread timing, file watching, JSONL tailing, and local runtime behavior:

- `tests/test_sense_hooks.py`
- `tests/test_sense_sqlite_watcher.py`
- `tests/test_sense_tailer.py`
- `tests/test_sense_writer.py`

Run them manually when changing `syke.observe.runtime` or adjacent ingestion/runtime code.

Suggested command:

```bash
uv run pytest \
  tests/test_sense_hooks.py \
  tests/test_sense_sqlite_watcher.py \
  tests/test_sense_tailer.py \
  tests/test_sense_writer.py -v
```

These are already part of the managed suite; run them directly when you want a faster local feedback loop on runtime changes.

## PR Expectations

Every PR should say which lane(s) were run.

- Always include the managed suite unless the change is docs-only.
- Add the Pi integration lane when provider/runtime behavior changed.
- Add focused targeted runs when they sharpen coverage for the subsystem you changed.
- If you skip a relevant lane, say so explicitly.
