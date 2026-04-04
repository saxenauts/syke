## What

Brief description of the change.

## How to test

State the exact lane(s) you ran and the command(s) used.

- Managed suite: `uv run pytest tests -v --tb=short`
- Pi integration lane when runtime/provider behavior changed: `SYKE_RUN_PI_INTEGRATION=1 uv run pytest tests/test_pi_integration.py -v`
- Any focused targeted runs or manual validation beyond pytest

## Checklist

- [ ] Managed suite passes with the canonical command above
- [ ] Additional lane coverage is listed when runtime, provider, or harness behavior changed
- [ ] New tests do not read from or write to real `~/.syke` or `~/.config/syke` state
- [ ] No new dependencies without justification
- [ ] Docs updated if behavior changed
