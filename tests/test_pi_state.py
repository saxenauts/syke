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


def test_build_pi_agent_env_points_pi_at_syke_owned_state(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "pi-agent"
    monkeypatch.setenv("SYKE_PI_AGENT_DIR", str(root))

    env = pi_state.build_pi_agent_env()

    assert env["PI_CODING_AGENT_DIR"] == str(root.resolve())
