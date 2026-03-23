# Adapter Maintainer

Short operating note for agents maintaining harness connections.

The deep reference is [../ADAPTER-PROTOCOL.md](../ADAPTER-PROTOCOL.md).

---

## Responsibilities

- connect a new harness
- check health
- heal broken adapters
- verify loop closure

---

## Preferred Mode

Use the factory-first path whenever possible:

- descriptor
- factory generation or repair
- dynamic adapter runtime

Use manual adapter edits only when the descriptor/factory path is insufficient.

---

## Health Loop

1. Check `HarnessRegistry().check_all_health()`.
2. If a harness degraded, inspect the latest real data.
3. Prefer descriptor/factory repair first.
4. Re-run ingest and health checks.
5. Confirm external ID stability before considering the repair done.

---

## Verify

A harness is meaningfully connected when:

1. ingest works
2. health is good
3. connector skill is installed where relevant
4. context skill is installed where relevant
5. the runtime path matches the current branch architecture

---

## Read Next

- [../ADAPTER-PROTOCOL.md](../ADAPTER-PROTOCOL.md)
- [adapter-connection.md](./adapter-connection.md)
