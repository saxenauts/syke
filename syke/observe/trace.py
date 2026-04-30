"""Self-observation status.

Rollout traces in ``syke.db`` are the canonical self-observation substrate.
Traces are always written — there is no disable gate.
"""

from __future__ import annotations

from syke.trace_store import trace_store_status

__all__ = ["trace_store_status"]
