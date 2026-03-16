"""Sense analyzer — infer format schema from sample data."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ISO_8601_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
EPOCH_PATTERN = re.compile(r"^1[0-9]{9}(\.\d+)?$")
ROLE_KEYS = {"role", "type", "sender", "author"}
CONTENT_KEYS = {"content", "message", "text", "body"}
TOOL_KEYS = {"tool_use", "function_call", "tool_call", "tool_result", "tool_calls"}
TIMESTAMP_KEYS = {"timestamp", "created_at", "time", "date", "ts", "createdAt", "updated_at"}


@dataclass
class AnalysisResult:
    format: str  # "jsonl" | "json" | "sqlite" | "unknown"
    timestamp_field: str | None = None
    role_field: str | None = None
    content_field: str | None = None
    tool_fields: list[str] = field(default_factory=list)
    confidence: float = 0.0


class SenseAnalyzer:
    def analyze(self, samples: list[str]) -> AnalysisResult:
        if not samples:
            return AnalysisResult(format="unknown")

        # Detect format
        parsed: list[dict] = []
        for line in samples:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    parsed.append(obj)
            except (json.JSONDecodeError, ValueError):
                pass

        if not parsed:
            # Check if it's sqlite magic bytes
            if samples and samples[0].startswith("SQLite format"):
                return AnalysisResult(format="sqlite", confidence=0.9)
            return AnalysisResult(format="unknown")

        fmt = "jsonl" if len(parsed) > 1 else "json"

        # Find fields across all parsed objects
        all_keys: set[str] = set()
        for obj in parsed:
            all_keys.update(self._flatten_keys(obj))

        ts_field = self._find_field(all_keys, TIMESTAMP_KEYS, parsed)
        role_field = self._find_field(all_keys, ROLE_KEYS, parsed)
        content_field = self._find_field(all_keys, CONTENT_KEYS, parsed)
        tool_fields = [k for k in all_keys if k in TOOL_KEYS]

        confidence = 0.3
        if ts_field:
            confidence += 0.2
        if role_field:
            confidence += 0.2
        if content_field:
            confidence += 0.2
        if tool_fields:
            confidence += 0.1

        return AnalysisResult(
            format=fmt,
            timestamp_field=ts_field,
            role_field=role_field,
            content_field=content_field,
            tool_fields=tool_fields,
            confidence=min(confidence, 1.0),
        )

    def _flatten_keys(self, obj: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            keys.add(k)
            keys.add(full)
            if isinstance(v, dict):
                keys.update(self._flatten_keys(v, full))
        return keys

    def _find_field(
        self, all_keys: set[str], candidates: set[str], parsed: list[dict]
    ) -> str | None:
        for key in candidates:
            if key in all_keys:
                return key
        return None
