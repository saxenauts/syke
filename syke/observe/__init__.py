"""observe — Syke's data capture layer. Turns AI harness activity into an immutable event timeline.

Public API available via direct submodule imports:
    from syke.observe.adapter import ObserveAdapter, ObservedSession, ObservedTurn
    from syke.observe.registry import HarnessRegistry
    from syke.observe.runtime import SenseWatcher, SenseWriter, SQLiteWatcher
    from syke.observe.trace import SykeObserver
    from syke.observe.importers import ChatGPTAdapter, IngestGateway
"""
