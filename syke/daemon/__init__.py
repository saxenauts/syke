"""Background sync daemon for Syke."""

from syke.daemon.daemon import (
    install_and_start,
    stop_and_unload,
    get_status,
)

__all__ = ["install_and_start", "stop_and_unload", "get_status"]
