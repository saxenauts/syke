"""Background sync daemon for Syke."""

from syke.daemon.daemon import (
    get_status,
    install_and_start,
    stop_and_unload,
)

__all__ = ["install_and_start", "stop_and_unload", "get_status"]
