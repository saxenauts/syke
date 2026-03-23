# Connecting a New Harness to Syke

Short entry point for connecting a harness on the current branch.

The full contract lives in [../ADAPTER-PROTOCOL.md](../ADAPTER-PROTOCOL.md).

---

## Default Path

Prefer:

1. descriptor
2. factory generation/test/deploy
3. dynamic adapter runtime

Manual adapter code is fallback, not default.

---

## Minimal Workflow

1. Find the local data path.
2. Identify the format cluster: JSONL, JSON, SQLite, multi-file, markdown.
3. Add or refine the descriptor.
4. Use `syke.observe.factory` to generate/test/deploy.
5. Validate ingest and health against real local data.

If the format cannot be expressed cleanly through the descriptor/factory path, write a manual adapter and integrate it with the current runtime.

---

## Hard Rules

- No LLM calls in the ingest runtime.
- No content caps or semantic rewriting in the adapter.
- If the harness does not provide a field, store `NULL`/empty rather than inventing data.
- Same input must produce the same events and external IDs.

---

## Read Next

- [../ADAPTER-PROTOCOL.md](../ADAPTER-PROTOCOL.md)
- [../OBSERVE-PRINCIPLES.md](../OBSERVE-PRINCIPLES.md)
- [../OBSERVE-ARCHITECTURE.md](../OBSERVE-ARCHITECTURE.md)
