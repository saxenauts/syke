from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from syke import pi_state


def test_pi_agent_dir_respects_env_override(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "pi-agent"
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(target))

    assert pi_state.get_pi_agent_dir() == target.resolve()
    assert pi_state.get_pi_auth_path() == target.resolve() / "auth.json"
    assert pi_state.get_pi_settings_path() == target.resolve() / "settings.json"
    assert pi_state.get_pi_models_path() == target.resolve() / "models.json"


def test_pi_agent_dir_defaults_to_syke_owned_pi_agent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SYKE_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(pi_state.config, "SYKE_HOME", tmp_path / ".syke")

    assert pi_state.get_pi_agent_dir() == (tmp_path / ".syke" / "pi-agent").resolve()


def test_set_api_key_writes_pi_auth_json_schema(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))

    pi_state.set_api_key("openrouter", "sk-or-test")

    data = json.loads(pi_state.get_pi_auth_path().read_text(encoding="utf-8"))
    assert data == {"openrouter": {"type": "api_key", "key": "sk-or-test"}}

    mode = oct(stat.S_IMODE(os.stat(pi_state.get_pi_auth_path()).st_mode))
    assert mode == "0o600"


def test_default_provider_and_model_are_stored_in_pi_settings(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))

    pi_state.set_default_provider("openai")
    pi_state.set_default_model("gpt-5.4")

    settings = json.loads(pi_state.get_pi_settings_path().read_text(encoding="utf-8"))
    assert settings["defaultProvider"] == "openai"
    assert settings["defaultModel"] == "gpt-5.4"
    assert pi_state.get_default_provider() == "openai"
    assert pi_state.get_default_model() == "gpt-5.4"


def test_remove_credential_clears_active_provider_and_model_when_active(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))

    pi_state.set_api_key("anthropic", "sk-ant-test")
    pi_state.set_default_provider("anthropic")
    pi_state.set_default_model("claude-sonnet-4-6")

    removed = pi_state.remove_credential("anthropic")

    assert removed is True
    settings = json.loads(pi_state.get_pi_settings_path().read_text(encoding="utf-8"))
    assert "defaultProvider" not in settings
    assert "defaultModel" not in settings


def test_remove_credential_leaves_other_active_provider_intact(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))

    pi_state.set_api_key("anthropic", "sk-ant-test")
    pi_state.set_default_provider("openai-codex")
    pi_state.set_default_model("gpt-5.4")

    removed = pi_state.remove_credential("anthropic")

    assert removed is True
    settings = json.loads(pi_state.get_pi_settings_path().read_text(encoding="utf-8"))
    assert settings["defaultProvider"] == "openai-codex"
    assert settings["defaultModel"] == "gpt-5.4"


def test_upsert_provider_override_writes_pi_models_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))

    pi_state.upsert_provider_override("azure-openai-responses", base_url="https://azure.example.com")

    models = json.loads(pi_state.get_pi_models_path().read_text(encoding="utf-8"))
    assert models == {
        "providers": {
            "azure-openai-responses": {
                "baseUrl": "https://azure.example.com",
            }
        }
    }


def test_build_pi_agent_env_uses_override_when_explicitly_set(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "pi-agent"
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(root))

    env = pi_state.build_pi_agent_env()

    assert env["PI_CODING_AGENT_DIR"] == str(root.resolve())


def test_build_pi_agent_env_points_pi_at_syke_owned_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SYKE_PI_AGENT_DIR", raising=False)
    monkeypatch.setattr(pi_state.config, "SYKE_HOME", tmp_path / ".syke")

    env = pi_state.build_pi_agent_env()

    assert env["PI_CODING_AGENT_DIR"] == str((tmp_path / ".syke" / "pi-agent").resolve())


def test_loads_legacy_native_pi_state_once_when_syke_state_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SYKE_PI_AGENT_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(tmp_path / "pi-state-audit.log"))
    monkeypatch.setattr(pi_state.config, "SYKE_HOME", tmp_path / ".syke")

    legacy_root = tmp_path / ".pi" / "agent"
    legacy_root.mkdir(parents=True, exist_ok=True)
    (legacy_root / "settings.json").write_text(
        json.dumps({"defaultProvider": "kimi-coding", "defaultModel": "k2p5"}) + "\n",
        encoding="utf-8",
    )

    assert pi_state.get_default_provider() == "kimi-coding"
    assert pi_state.get_default_model() == "k2p5"

    target = tmp_path / ".syke" / "pi-agent" / "settings.json"
    assert target.exists()
    migrated = json.loads(target.read_text(encoding="utf-8"))
    assert migrated["defaultProvider"] == "kimi-coding"
    assert migrated["defaultModel"] == "k2p5"

    audit_lines = (tmp_path / "pi-state-audit.log").read_text(encoding="utf-8").splitlines()
    payload = json.loads(audit_lines[-1])
    assert payload["event"] == "migrate_legacy_pi_state"


def test_setting_default_provider_writes_audit_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(tmp_path / "pi-state-audit.log"))

    pi_state.set_default_provider("openai-codex")

    audit_lines = (tmp_path / "pi-state-audit.log").read_text(encoding="utf-8").splitlines()
    assert audit_lines
    payload = json.loads(audit_lines[-1])
    assert payload["event"] == "set_default_provider"
    assert payload["after"]["defaultProvider"] == "openai-codex"
    assert payload["path"].endswith("settings.json")
    assert payload["stack"]


def test_setting_api_key_writes_audit_entry(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(tmp_path / "pi-agent"))
    monkeypatch.setenv("SYKE_PI_STATE_AUDIT_PATH", str(tmp_path / "pi-state-audit.log"))

    pi_state.set_api_key("openrouter", "sk-or-test")

    audit_lines = (tmp_path / "pi-state-audit.log").read_text(encoding="utf-8").splitlines()
    assert audit_lines
    payload = json.loads(audit_lines[-1])
    assert payload["event"] == "set_api_key"
    assert payload["after"]["openrouter"]["type"] == "api_key"
    assert payload["path"].endswith("auth.json")
