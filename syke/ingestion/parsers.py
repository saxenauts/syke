"""Universal parsing helpers for Observe adapters.

These are mechanical extraction functions — no LLM, no heuristics.
Each adapter (compiler) uses these to normalize harness-native formats
into the canonical event schema.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from syke.ingestion.constants import CHARS_PER_TOKEN_ESTIMATE

logger = logging.getLogger(__name__)


def read_jsonl(fpath: Path) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    skipped = 0
    for raw in fpath.open():
        raw = raw.strip()
        if not raw:
            continue
        try:
            lines.append(cast(dict[str, object], json.loads(raw)))
        except json.JSONDecodeError:
            skipped += 1
    if skipped and not lines:
        logger.warning("File %s: all %d lines failed JSON parse", fpath.name, skipped)
    elif skipped:
        logger.debug(
            "File %s: skipped %d malformed lines (%d valid)", fpath.name, skipped, len(lines)
        )
    return lines


def parse_timestamp(line: dict[str, object]) -> datetime | None:
    ts = line.get("timestamp", "")
    if not ts:
        return None
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000, tz=UTC)
        except (ValueError, OSError):
            return None
    return None


def extract_text_content(line: dict[str, object]) -> str:
    msg_obj = line.get("message", {})
    if isinstance(msg_obj, dict):
        msg = cast(dict[str, object], msg_obj)
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return _extract_text_blocks(cast(list[object], content))

    content = line.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _extract_text_blocks(cast(list[object], content))

    return ""


def extract_tool_blocks(line: dict[str, object]) -> list[dict[str, object]]:
    blocks_obj: object = []
    msg_obj = line.get("message", {})
    if isinstance(msg_obj, dict):
        msg = cast(dict[str, object], msg_obj)
        blocks_obj = msg.get("content", [])
    elif isinstance(line.get("content"), list):
        blocks_obj = line.get("content", [])

    if not isinstance(blocks_obj, list):
        return []

    tool_blocks: list[dict[str, object]] = []
    for block_obj in cast(list[object], blocks_obj):
        if not isinstance(block_obj, dict):
            continue

        block = cast(dict[str, object], block_obj)
        btype = block.get("type", "")

        if btype == "tool_use":
            tool_name = block.get("name")
            tool_id = block.get("id")
            tool_input = block.get("input", {})
            if not isinstance(tool_name, str) or not tool_name:
                continue
            if not isinstance(tool_id, str) or not tool_id:
                continue
            if not isinstance(tool_input, dict):
                tool_input = {}

            tool_blocks.append(
                {
                    "block_type": "tool_use",
                    "tool_name": tool_name,
                    "tool_id": tool_id,
                    "input": cast(dict[str, object], tool_input),
                }
            )

        elif btype == "tool_result":
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue

            content = block.get("content", "")
            if isinstance(content, str):
                content_text = content
            elif isinstance(content, list):
                content_text = _flatten_tool_result(cast(list[object], content))
            else:
                content_text = ""

            tool_blocks.append(
                {
                    "block_type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content_text,
                    "is_error": bool(block.get("is_error", False)),
                }
            )

    return tool_blocks


def _extract_text_blocks(blocks: list[object]) -> str:
    parts: list[str] = []
    for block_obj in blocks:
        if isinstance(block_obj, str):
            parts.append(block_obj)
            continue
        if not isinstance(block_obj, dict):
            continue
        block = cast(dict[str, object], block_obj)
        btype = block.get("type", "")

        if btype == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                parts.append(text)

        elif btype == "thinking":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(f"[thinking]\n{text}")

    return "\n".join(parts)


def _flatten_tool_result(blocks: list[object]) -> str:
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict):
            text = cast(dict[str, object], b).get("text", "")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def decode_project_dir(dirname: str) -> str:
    raw = dirname.lstrip("-")
    tokens = raw.split("-")

    resolved = resolve_path_dfs(Path("/"), tokens, 0)
    if resolved is None:
        path = "/" + dirname.lstrip("-").replace("-", "/")
    else:
        path = str(resolved)

    home = str(Path.home())
    if path.startswith(home + "/"):
        path = "~/" + path[len(home) + 1 :]
    elif path == home:
        path = "~"
    return path


def resolve_path_dfs(base: Path, tokens: list[str], idx: int) -> Path | None:
    if idx == len(tokens):
        return base if base.is_dir() else None

    for end in range(idx + 1, len(tokens) + 1):
        segment_hyphen = "-".join(tokens[idx:end])
        candidate = base / segment_hyphen
        if candidate.is_dir():
            result = resolve_path_dfs(candidate, tokens, end)
            if result is not None:
                return result

        if end > idx + 1:
            segment_space = " ".join(tokens[idx:end])
            candidate = base / segment_space
            if candidate.is_dir():
                result = resolve_path_dfs(candidate, tokens, end)
                if result is not None:
                    return result

    return None


def measure_content(text: str) -> tuple[int, int]:
    chars = len(text)
    estimated_tokens = chars // CHARS_PER_TOKEN_ESTIMATE
    return chars, estimated_tokens


def read_json(fpath: Path) -> dict[str, object] | None:
    """Read a single JSON file, returning None on parse error.

    Args:
        fpath: Path to JSON file

    Returns:
        Parsed JSON dict, or None if file not found or JSON is invalid.
        Logs warnings on failures.
    """
    try:
        content = fpath.read_text(encoding="utf-8")
        return cast(dict[str, object], json.loads(content))
    except FileNotFoundError:
        logger.warning("File not found: %s", fpath)
        return None
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse JSON in %s: %s", fpath.name, e)
        return None


def extract_field(obj: Mapping[str, object], dotted_path: str) -> object | None:
    """Extract a nested field using dot-separated path.

    Args:
        obj: Dictionary to extract from
        dotted_path: Dot-separated path (e.g., "a.b.c")

    Returns:
        The value at the path, or None if any step fails or path is empty.

    Examples:
        extract_field({"a": {"b": {"c": 42}}}, "a.b.c") → 42
        extract_field({"a": {"b": 1}}, "a.b.c") → None
        extract_field({}, "a.b") → None
    """
    if not dotted_path:
        return None

    current: object = obj
    for key in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
        if current is None:
            return None

    return current


def normalize_role(raw: str, mapping: dict[str, str] | None = None) -> str:
    """Normalize a role string to canonical form.

    Default mapping: {"human": "user", "ai": "assistant", "bot": "assistant"}
    Custom mapping overrides defaults.

    Args:
        raw: Raw role string
        mapping: Optional custom mapping dict

    Returns:
        Normalized role string (lowercase). Falls through to raw.lower() if no match.
    """
    default_mapping = {"human": "user", "ai": "assistant", "bot": "assistant"}

    if mapping:
        default_mapping.update(mapping)

    normalized = raw.lower()
    return default_mapping.get(normalized, normalized)
