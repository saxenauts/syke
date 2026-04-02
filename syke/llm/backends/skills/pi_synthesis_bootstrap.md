BOOTSTRAP CONTEXT

This is first-run synthesis during setup.
Treat this cycle as initial memex formation, not a normal incremental refresh.

Current bootstrap state:
- pending events since cursor: __PENDING_COUNT__
- existing memex: none or not trustworthy enough for reuse
- connected sources:
__SOURCE_BLOCK__

Bootstrap priorities:
- start with a broad durable survey before drilling into details
- identify the smallest set of active durable threads that explains the current state
- prefer creating a compact initial memory skeleton over overfitting to noisy trace churn
- keep the first memex navigable and useful now, even if many details remain for later cycles
- use the whole workspace as context, not only the newest few rows

---

