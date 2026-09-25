"""Exact, provider-independent measurement for MEMEX and per-memory budgets."""

from __future__ import annotations

import os
from functools import lru_cache

import tiktoken

MEMEX_TOKEN_ENCODING = "o200k_base"
MEMEX_TOKEN_LIMIT = 2_000
MEMORY_TOKEN_LIMIT = 2_000


@lru_cache(maxsize=1)
def _memex_encoding() -> tiktoken.Encoding:
    if "TIKTOKEN_CACHE_DIR" not in os.environ and "DATA_GYM_CACHE_DIR" not in os.environ:
        from syke.config import user_control_dir

        cache_dir = user_control_dir("") / "tokenizers"
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            cache_dir.chmod(0o700)
        except OSError:
            pass
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    return tiktoken.get_encoding(MEMEX_TOKEN_ENCODING)


def warm_memex_tokenizer() -> None:
    """Resolve the tokenizer during trusted workspace initialization."""
    _memex_encoding()


def strip_memex_header(content: str) -> str:
    """Remove the routed projection header, leaving canonical MEMEX content."""
    lines = content.split("\n")
    if lines and lines[0].startswith("# MEMEX ["):
        return "\n".join(lines[1:]).lstrip("\n")
    return content


def memex_body(content: str) -> str:
    return strip_memex_header(content).strip()


def count_memex_tokens(content: str) -> int:
    """Count canonical MEMEX tokens with the fixed public encoding."""
    return len(_memex_encoding().encode_ordinary(memex_body(content)))


def count_memory_tokens(content: str) -> int:
    """Count one graph memory's content with the MEMEX encoding."""
    return len(_memex_encoding().encode_ordinary(content))


def measure_memex(content: str) -> dict[str, int | str | bool]:
    tokens = count_memex_tokens(content)
    return {
        "tokens": tokens,
        "limit": MEMEX_TOKEN_LIMIT,
        "encoding": MEMEX_TOKEN_ENCODING,
        "fill_pct": min(100, round(tokens / MEMEX_TOKEN_LIMIT * 100)),
        "over_budget": tokens > MEMEX_TOKEN_LIMIT,
    }


def format_memex_projection(content: str) -> str:
    """Render canonical content with an exact, reproducible budget header."""
    body = memex_body(content)
    measurement = measure_memex(body)
    header = (
        f"# MEMEX [{measurement['tokens']:,} / {measurement['limit']:,} tokens "
        f"· {measurement['fill_pct']}% · {measurement['encoding']}]"
    )
    return f"{header}\n\n{body}"
