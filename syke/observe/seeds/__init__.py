from __future__ import annotations

from pathlib import Path


def get_seed_adapter_path(source: str) -> Path | None:
    seed_path = Path(__file__).parent / f"{source}.py"
    return seed_path if seed_path.is_file() else None


__all__ = ["get_seed_adapter_path"]
