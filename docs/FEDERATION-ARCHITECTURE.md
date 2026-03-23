# Federation Architecture

> Minimal note for the current branch. Federation is now mostly an Observe concern, not a separate architecture.

---

## Federation Invariants

1. **One schema, many adapters.** The events table is the IR. Adapters compile into it.
2. **Provenance on every event.** Source, source_path, source_event_type, adapter version.
3. **Time is the only correlation constant.** Cross-harness linking uses timestamps, not shared IDs.
4. **Observe doesn't link. Map links.** Session grouping across harnesses is a Map concern.
5. **Conflicts are data.** Store both sides. Never resolve at capture time.
6. **Adapters can be generated or manual. Schema stays stable.**

---

## Current Meaning

Right now, federation in Syke means:

- multiple harnesses can feed one timeline
- every event retains its source and provenance
- contradictions across harnesses are preserved, not resolved
- memex synthesis happens after federation, not during capture

For the operational architecture, see [OBSERVE-ARCHITECTURE.md](OBSERVE-ARCHITECTURE.md).

---

*Document version: minimal-note*
