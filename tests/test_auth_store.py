"""Tests for syke.llm.auth_store — AuthStore read/write, permissions, redaction."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from syke.llm.auth_store import AuthStore, _redact


class TestRedact:
    def test_empty_token_fully_redacted(self) -> None:
        assert _redact("") == "***"

    def test_short_token_shows_only_length(self) -> None:
        result = _redact("abc")
        assert "●●●" in result
        assert "(3 chars)" in result
        assert "abc" not in result

    def test_long_token_shows_only_length(self) -> None:
        token = "sk-or-v1-abcdefghij1234567890"
        result = _redact(token)
        assert "●●●" in result
        assert f"({len(token)} chars)" in result
        assert "sk-or" not in result


class TestAuthStoreReadWrite:
    def test_empty_store_returns_none(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        assert store.get_active_provider() is None
        assert store.get_token("openrouter") is None

    def test_set_and_get_token(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "sk-or-test-key")
        assert store.get_token("openrouter") == "sk-or-test-key"
        assert store.get_token("zai") is None
        store.set_token("openrouter", "new-key")
        assert store.get_token("openrouter") == "new-key"

    def test_set_active_provider(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_active_provider("openrouter")
        assert store.get_active_provider() == "openrouter"

    def test_remove_token(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "or-key")
        assert store.remove_token("openrouter") is True
        assert store.get_token("openrouter") is None

    def test_remove_active_provider_clears_active(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "or-key")
        store.set_active_provider("openrouter")
        store.remove_token("openrouter")
        assert store.get_active_provider() is None

    def test_list_providers(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "sk-or-v1-long-key-for-testing-display")
        store.set_token("zai", "zai-long-key-for-testing-display")
        store.set_active_provider("openrouter")

        listed = store.list_providers()
        assert "openrouter" in listed
        assert "zai" in listed
        assert listed["openrouter"]["active"] == "yes"
        assert listed["zai"]["active"] == ""
        assert "●●●" in listed["openrouter"]["credential"]
        assert "chars)" in listed["openrouter"]["credential"]

    def test_status_dict(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "key")
        store.set_active_provider("openrouter")

        s = store.status_dict()
        assert s["active_provider"] == "openrouter"
        assert "openrouter" in s["configured_providers"]
        assert s["has_file"] is True


class TestAuthStoreAtomicWrite:
    def test_file_permissions_0600(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "key")
        mode = oct(stat.S_IMODE(os.stat(store.path).st_mode))
        assert mode == "0o600"

    def test_schema_version(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "key")
        with open(store.path) as f:
            data = json.load(f)
        assert data["version"] == 1

    @pytest.mark.parametrize(
        "content",
        [
            "NOT JSON {{{{",
            json.dumps({"version": 99, "providers": {"x": {"auth_token": "y"}}}),
        ],
    )
    def test_corrupt_or_wrong_version_treated_as_empty(self, tmp_path: Path, content: str) -> None:
        p = tmp_path / "auth.json"
        p.write_text(content)
        store = AuthStore(p)
        assert store.get_active_provider() is None

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "auth.json"
        store = AuthStore(nested)
        store.set_token("openrouter", "key")
        assert nested.exists()
