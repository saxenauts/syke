from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from syke.db import SykeDB
from syke.observe.observe import ObserveAdapter, ObservedSession

logger = logging.getLogger(__name__)


class SQLiteQueryAdapter(ObserveAdapter):
    """Stub adapter for SQLite-based harnesses (Cursor, OpenCode, Windsurf).

    Not yet implemented. Registered in the harness registry for health tracking.
    Full implementation will read SQLite databases using descriptor-provided
    session_query and turn_query SQL, with column maps for field extraction.
    """

    source: str = "sqlite-stub"

    def __init__(self, db: SykeDB, user_id: str, source_name: str = "sqlite-stub"):
        self.source = source_name
        super().__init__(db, user_id)

    def discover(self) -> list[Path]:
        logger.info("SQLiteQueryAdapter.discover() not yet implemented for %s", self.source)
        return []

    def iter_sessions(self, since: float = 0) -> Iterable[ObservedSession]:
        logger.info("SQLiteQueryAdapter.iter_sessions() not yet implemented for %s", self.source)
        return iter([])
