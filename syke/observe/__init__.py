"""observe — Syke's harness discovery and adapter surface.

The agent reads harness data directly via adapter markdowns installed at
~/.syke/adapters/{source}.md.

Public API:
    from syke.observe.catalog import active_sources, get_source
    from syke.observe.bootstrap import ensure_adapters
    from syke.observe.trace import self_observation_status
"""
