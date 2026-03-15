# Adapter Maintainer

Manages Syke's harness connections. Four capabilities: install, check, heal, verify.

## Install — Connect a New Harness

When someone says "I use X, it's at this path" — follow `docs/skills/adapter-connection.md`. The 6-step recipe produces: an adapter file, a TOML descriptor, and a registry entry.

After the adapter works, create two SKILL.md packages for the harness:

**`syke-observe-<harness>/SKILL.md`**: Install instructions for the inbound connection. Contains transport setup (which hooks to enable, which files to watch), health check commands, and the descriptor reference.

**`syke-context/SKILL.md`**: Already exists as the universal outbound skill. Verify it's installed in the harness's skill directory.

## Check — Health Check All Harnesses

```python
from syke.ingestion.registry import HarnessRegistry
registry = HarnessRegistry()
health = registry.check_all_health()
for source, h in sorted(health.items()):
    print(f"{source}: {h.status} (files={h.files_found})")
```

Healthy means: data path exists, files/DB match patterns, most recent artifact parses. Report any harness that's degraded.

Also check: is the connector skill installed in the harness? Is the context skill installed?

## Heal — Fix a Broken Adapter

When health check reports `parse_error` or `no_data` for a previously-healthy harness:

1. Read the error from HarnessHealth.error
2. Sample the latest data file — has the format changed?
3. Compare against the current adapter's parsing expectations
4. If format changed: update the adapter to handle the new format
5. If data moved: update the descriptor paths
6. Validate: `adapter.ingest()` produces events > 0
7. Validate: external_id stability — same input must produce same external_ids

Never break idempotency. The external_id contract is sacred.

## Verify — Confirm Loop Closure

A harness is fully connected when ALL of these are true:

1. Adapter exists and `ingest()` returns events > 0
2. Connector skill (`syke-observe-<harness>/`) is installed in the harness
3. Context skill (`syke-context/`) is installed in the harness  
4. Health check reports "healthy"
5. Real-time transport is configured (hook or watch or native)

Run verification:
```bash
# Check adapter works
syke sync --source <harness>

# Check skills installed
ls <harness-skills-dir>/syke-context/SKILL.md
ls <harness-skills-dir>/syke-observe-<harness>/SKILL.md

# Check health
python -c "from syke.ingestion.registry import HarnessRegistry; print(HarnessRegistry().check_health('<harness>'))"
```

## Constraints

- Never add LLM calls to adapters (P1: No Inferred Semantics)
- Never cap content (P4: Raw Preservation)
- Never break external_id stability (P6: Idempotent Ingestion)
- The adapter is deterministic. The maintainer skill is the intelligent part.
