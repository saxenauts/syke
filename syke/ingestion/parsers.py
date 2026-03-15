"""Universal parsing helpers for Observe adapters.

These are mechanical extraction functions — no LLM, no heuristics.
Each adapter (compiler) uses these to normalize harness-native formats
into the canonical event schema.
"""

from __future__ import annotations

import json
import logging
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
            return _extract_blocks(cast(list[object], content))

    content = line.get("content", "")
    if isinstance(content, str):
        return content

    return ""


def _extract_blocks(blocks: list[object]) -> str:
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

        elif btype == "tool_use":
            name = block.get("name", "unknown")
            tool_id = block.get("id", "")
            inp = block.get("input", {})
            inp_str = json.dumps(inp, default=str, ensure_ascii=False) if inp else "{}"
            id_tag = f" id={tool_id}" if tool_id else ""
            parts.append(f"[tool_use: {name}{id_tag}]\n{inp_str}")

        elif btype == "tool_result":
            tool_use_id = block.get("tool_use_id", "")
            is_error = block.get("is_error", False)
            content = block.get("content", "")
            id_tag = f" for={tool_use_id}" if tool_use_id else ""
            err_tag = " ERROR" if is_error else ""
            if isinstance(content, str):
                parts.append(f"[tool_result{id_tag}{err_tag}]\n{content}")
            elif isinstance(content, list):
                flat = _flatten_tool_result(cast(list[object], content))
                parts.append(f"[tool_result{id_tag}{err_tag}]\n{flat}")

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
