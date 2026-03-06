"""Tests for syke.llm.auth_store — AuthStore read/write, permissions, redaction."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from syke.llm.auth_store import AuthStore, _redact


class TestRedact:
    def test_short_token_fully_redacted(self) -> None:
        assert _redact("abc") == "***"
        assert _redact("123456789012") == "***"

    def test_long_token_shows_prefix_suffix(self) -> None:
        token = "sk-or-v1-abcdefghij1234567890"
        result = _redact(token)
        assert result.startswith("sk-or-")
        assert "...7890" in result
        assert f"{len(token)} chars" in result

    def test_empty_token(self) -> None:
        assert _redact("") == "***"


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

    def test_set_active_provider(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_active_provider("openrouter")
        assert store.get_active_provider() == "openrouter"

    def test_overwrite_token(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "old-key")
        store.set_token("openrouter", "new-key")
        assert store.get_token("openrouter") == "new-key"

    def test_multiple_providers(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "or-key")
        store.set_token("zai", "zai-key")
        assert store.get_token("openrouter") == "or-key"
        assert store.get_token("zai") == "zai-key"

    def test_remove_token(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "or-key")
        assert store.remove_token("openrouter") is True
        assert store.get_token("openrouter") is None

    def test_remove_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = AuthStore(tmp_path / "auth.json")
        assert store.remove_token("nonexistent") is False

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
        assert "..." in listed["openrouter"]["credential"]

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

    def test_corrupt_file_treated_as_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "auth.json"
        p.write_text("NOT JSON {{{{")
        store = AuthStore(p)
        assert store.get_active_provider() is None
        assert store.get_token("openrouter") is None

    def test_wrong_version_treated_as_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "auth.json"
        p.write_text(
            json.dumps({"version": 99, "providers": {"x": {"auth_token": "y"}}})
        )
        store = AuthStore(p)
        assert store.get_token("x") is None

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "auth.json"
        store = AuthStore(nested)
        store.set_token("openrouter", "key")
        assert nested.exists()


class TestAuthStoreResolution:
    """Test that resolve_provider and _resolve_token read from auth.json."""

    def test_resolve_provider_reads_auth_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYKE_PROVIDER", raising=False)

        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "or-key")
        store.set_active_provider("openrouter")

        from syke.llm.env import resolve_provider
        from unittest.mock import patch

        with patch("syke.llm.env._get_auth_store", return_value=store):
            with patch("syke.llm.env._claude_login_available", return_value=False):
                spec = resolve_provider()
        assert spec.id == "openrouter"

    def test_resolve_token_from_auth_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYKE_OPENROUTER_API_KEY", raising=False)

        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "stored-key-123")

        from syke.llm.env import build_agent_env
        from syke.llm.providers import PROVIDERS
        from unittest.mock import patch

        with patch("syke.llm.env._get_auth_store", return_value=store):
            env = build_agent_env(PROVIDERS["openrouter"])
        assert env["ANTHROPIC_AUTH_TOKEN"] == "stored-key-123"

    def test_env_var_overrides_auth_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYKE_OPENROUTER_API_KEY", "env-key")

        store = AuthStore(tmp_path / "auth.json")
        store.set_token("openrouter", "stored-key")

        from syke.llm.env import build_agent_env
        from syke.llm.providers import PROVIDERS
        from unittest.mock import patch

        with patch("syke.llm.env._get_auth_store", return_value=store):
            env = build_agent_env(PROVIDERS["openrouter"])
        assert env["ANTHROPIC_AUTH_TOKEN"] == "env-key"

    def test_cli_flag_overrides_auth_json_active(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SYKE_PROVIDER", raising=False)

        store = AuthStore(tmp_path / "auth.json")
        store.set_active_provider("openrouter")

        from syke.llm.env import resolve_provider
        from unittest.mock import patch

        with patch("syke.llm.env._get_auth_store", return_value=store):
            spec = resolve_provider(cli_provider="zai")
        assert spec.id == "zai"

    def test_syke_provider_env_overrides_auth_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYKE_PROVIDER", "zai")

        store = AuthStore(tmp_path / "auth.json")
        store.set_active_provider("openrouter")

        from syke.llm.env import resolve_provider
        from unittest.mock import patch

        with patch("syke.llm.env._get_auth_store", return_value=store):
            spec = resolve_provider()
        assert spec.id == "zai"
