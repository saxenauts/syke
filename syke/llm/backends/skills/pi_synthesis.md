A scheduled Syke synthesis cycle has started.

The MEMEX above is your prior — you wrote it last cycle. Continue from there;
do not re-derive numbers, timestamps, or claims already in it.

syke.db is the source of truth, MEMEX is its projection. Query the DB only
when you intend to write.

The canonical MEMEX row in syke.db uses `source_event_ids = ["__memex__"]`.
Never delete or deactivate that row as ordinary cleanup. If nothing changed,
leave the canonical row active and project the same MEMEX forward.

The PSYCHE block above lists each harness and where its data lives. That
layout is stable across cycles.

Update memories and MEMEX if state has actually changed. This cycle's job
is to keep the durable memory map current.
