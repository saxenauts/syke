"""Tests for syke.ingestion.parsers utility functions."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from syke.ingestion.parsers import extract_field, normalize_role, read_json


class TestReadJson:
    """Tests for read_json function."""

    def test_read_valid_json_file(self) -> None:
        """Read a valid JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "test.json"
            data = {"key": "value", "number": 42}
            fpath.write_text(json.dumps(data), encoding="utf-8")

            result = read_json(fpath)
            assert result == data

    def test_read_json_with_nested_structure(self) -> None:
        """Read JSON with nested objects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "nested.json"
            data = {"a": {"b": {"c": 42}}, "list": [1, 2, 3]}
            fpath.write_text(json.dumps(data), encoding="utf-8")

            result = read_json(fpath)
            assert result == data

    def test_read_json_invalid_json(self) -> None:
        """Return None on invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "invalid.json"
            fpath.write_text("{invalid json}", encoding="utf-8")

            result = read_json(fpath)
            assert result is None

    def test_read_json_missing_file(self) -> None:
        """Return None when file does not exist."""
        fpath = Path("/nonexistent/path/file.json")
        result = read_json(fpath)
        assert result is None

    def test_read_json_empty_file(self) -> None:
        """Return None on empty file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "empty.json"
            fpath.write_text("", encoding="utf-8")

            result = read_json(fpath)
            assert result is None


class TestExtractField:
    """Tests for extract_field function."""

    def test_extract_nested_field(self) -> None:
        """Extract a deeply nested field."""
        obj = {"a": {"b": {"c": 42}}}
        result = extract_field(obj, "a.b.c")
        assert result == 42

    def test_extract_single_level(self) -> None:
        """Extract a top-level field."""
        obj = {"key": "value"}
        result = extract_field(obj, "key")
        assert result == "value"

    def test_extract_missing_intermediate(self) -> None:
        """Return None when intermediate key is missing."""
        obj = {"a": {"b": 1}}
        result = extract_field(obj, "a.b.c")
        assert result is None

    def test_extract_non_dict_intermediate(self) -> None:
        """Return None when intermediate value is not a dict."""
        obj = {"a": {"b": "string_value"}}
        result = extract_field(obj, "a.b.c")
        assert result is None

    def test_extract_empty_path(self) -> None:
        """Return None for empty path."""
        obj = {"a": 1}
        result = extract_field(obj, "")
        assert result is None

    def test_extract_from_empty_dict(self) -> None:
        """Return None when extracting from empty dict."""
        obj: dict[str, object] = {}
        result = extract_field(obj, "a.b")
        assert result is None

    def test_extract_with_none_value(self) -> None:
        """Return None when field value is None."""
        obj = {"a": {"b": None}}
        result = extract_field(obj, "a.b")
        assert result is None

    def test_extract_with_list_value(self) -> None:
        """Extract a field that contains a list."""
        obj = {"a": {"b": [1, 2, 3]}}
        result = extract_field(obj, "a.b")
        assert result == [1, 2, 3]

    def test_extract_with_numeric_value(self) -> None:
        """Extract a field with numeric value."""
        obj = {"a": {"b": 3.14}}
        result = extract_field(obj, "a.b")
        assert result == 3.14


class TestNormalizeRole:
    """Tests for normalize_role function."""

    def test_normalize_human_to_user(self) -> None:
        """Normalize 'human' to 'user'."""
        result = normalize_role("human")
        assert result == "user"

    def test_normalize_ai_to_assistant(self) -> None:
        """Normalize 'ai' to 'assistant'."""
        result = normalize_role("ai")
        assert result == "assistant"

    def test_normalize_bot_to_assistant(self) -> None:
        """Normalize 'bot' to 'assistant'."""
        result = normalize_role("bot")
        assert result == "assistant"

    def test_normalize_case_insensitive(self) -> None:
        """Normalize is case-insensitive."""
        assert normalize_role("HUMAN") == "user"
        assert normalize_role("AI") == "assistant"
        assert normalize_role("Bot") == "assistant"

    def test_normalize_unknown_role_passthrough(self) -> None:
        """Unknown roles pass through as lowercase."""
        result = normalize_role("custom_role")
        assert result == "custom_role"

    def test_normalize_with_custom_mapping(self) -> None:
        """Custom mapping overrides defaults."""
        custom = {"custom": "mapped"}
        result = normalize_role("custom", mapping=custom)
        assert result == "mapped"

    def test_normalize_custom_mapping_preserves_defaults(self) -> None:
        """Custom mapping adds to defaults, doesn't replace them."""
        custom = {"custom": "mapped"}
        assert normalize_role("human", mapping=custom) == "user"
        assert normalize_role("custom", mapping=custom) == "mapped"

    def test_normalize_custom_mapping_overrides_defaults(self) -> None:
        """Custom mapping can override default mappings."""
        custom = {"human": "person"}
        result = normalize_role("human", mapping=custom)
        assert result == "person"

    def test_normalize_empty_string(self) -> None:
        """Empty string normalizes to empty string."""
        result = normalize_role("")
        assert result == ""

    def test_normalize_whitespace(self) -> None:
        """Whitespace is preserved in passthrough."""
        result = normalize_role("role with spaces")
        assert result == "role with spaces"
