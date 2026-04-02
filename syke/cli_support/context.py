"""Shared CLI runtime context helpers."""

from __future__ import annotations

from syke.config import user_events_db_path, user_syke_db_path
from syke.db import SykeDB


def get_db(user_id: str) -> SykeDB:
    """Get an initialized DB for a user."""
    return SykeDB(user_syke_db_path(user_id), event_db_path=user_events_db_path(user_id))


def observe_registry(user_id: str):
    from syke.config import user_data_dir
    from syke.observe.registry import HarnessRegistry

    return HarnessRegistry(dynamic_adapters_dir=user_data_dir(user_id) / "adapters")

