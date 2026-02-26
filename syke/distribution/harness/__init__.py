"""Harness adapter registry — discovers and manages platform integrations.

Usage:
    from syke.distribution.harness import get_detected_adapters, install_all

    # During setup: install to all detected platforms
    results = install_all(memex=content)

    # Health check: show status of all known platforms
    statuses = status_all()
"""

from __future__ import annotations

import logging

from syke.distribution.harness.base import AdapterResult, AdapterStatus, HarnessAdapter

log = logging.getLogger(__name__)

# Lazy-loaded to avoid import overhead at startup
_adapter_classes: list[type[HarnessAdapter]] | None = None


def _load_adapters() -> list[type[HarnessAdapter]]:
    """Import and return all adapter classes."""
    global _adapter_classes
    if _adapter_classes is not None:
        return _adapter_classes

    from syke.distribution.harness.hermes import HermesAdapter
    from syke.distribution.harness.claude_desktop import ClaudeDesktopAdapter
    from syke.distribution.harness.pi import PiAdapter

    _adapter_classes = [
        HermesAdapter,
        ClaudeDesktopAdapter,
        PiAdapter,
        # TODO: AmpAdapter, RooAdapter, GooseAdapter — add when installed/needed
    ]
    return _adapter_classes


def get_all_adapters() -> list[HarnessAdapter]:
    """Instantiate all known harness adapters."""
    return [cls() for cls in _load_adapters()]


def get_detected_adapters() -> list[HarnessAdapter]:
    """Return adapters only for platforms that are actually installed."""
    return [a for a in get_all_adapters() if a.detect()]


def install_all(
    memex: str | None = None, skill_content: str | None = None
) -> dict[str, AdapterResult]:
    """Run install on all detected adapters.

    Returns {adapter_name: AdapterResult}.
    """
    results: dict[str, AdapterResult] = {}
    for adapter in get_detected_adapters():
        try:
            results[adapter.name] = adapter.install(
                memex=memex, skill_content=skill_content
            )
        except Exception as e:
            log.warning("Adapter %s install failed: %s", adapter.name, e)
            result = AdapterResult()
            result.warnings.append(str(e))
            results[adapter.name] = result
    return results


def status_all() -> list[AdapterStatus]:
    """Health check all known adapters (detected or not)."""
    statuses: list[AdapterStatus] = []
    for adapter in get_all_adapters():
        try:
            statuses.append(adapter.status())
        except Exception as e:
            log.warning("Adapter %s status failed: %s", adapter.name, e)
            statuses.append(
                AdapterStatus(
                    name=adapter.name,
                    detected=False,
                    connected=False,
                    native_memory=adapter.has_native_memory,
                    notes=f"Status check failed: {e}",
                )
            )
    return statuses


__all__ = [
    "HarnessAdapter",
    "AdapterResult",
    "AdapterStatus",
    "get_all_adapters",
    "get_detected_adapters",
    "install_all",
    "status_all",
]
