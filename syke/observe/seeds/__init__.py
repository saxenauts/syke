from __future__ import annotations

from pathlib import Path


def get_seed_adapter_md_path(source: str) -> Path | None:
    """Return the path to the adapter markdown guide for *source*, if it exists."""
    md_path = Path(__file__).parent / f"adapter-{source}.md"
    return md_path if md_path.is_file() else None


__all__ = ["get_seed_adapter_md_path"]
