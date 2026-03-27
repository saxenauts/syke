"""Pi workspace settings generation for Syke.

Syke treats Pi as the canonical runtime. This module writes project-local
`.pi/settings.json` so each Syke workspace launches Pi with the right provider,
model, thinking level, and any Syke-defined provider overrides.
"""

from __future__ import annotations

import json
from pathlib import Path

from syke.config import CFG, SYNC_THINKING
from syke.llm.env import build_pi_runtime_env, resolve_provider, _resolve_provider_config
from syke.llm.providers import ProviderSpec


def _thinking_level_from_budget(thinking_budget: int) -> str:
    if thinking_budget <= 0:
        return "off"
    if thinking_budget <= 1024:
        return "minimal"
    if thinking_budget <= 4096:
        return "low"
    if thinking_budget <= 12000:
        return "medium"
    if thinking_budget <= 32000:
        return "high"
    return "xhigh"


def _resolve_model_name(
    provider_config: dict[str, str],
    model_override: str | None,
) -> str:
    if model_override:
        return model_override
    if provider_config.get("model"):
        return provider_config["model"]
    return CFG.models.synthesis or "sonnet"


def _render_openai_override_extension(base_url: str) -> str:
    return f"""export default function (pi) {{
  pi.registerProvider("openai", {{
    baseUrl: {json.dumps(base_url)}
  }});
}}
"""


def _render_openai_compatible_extension(
    provider_name: str,
    *,
    base_url: str,
    model_name: str,
    api_key_env_var: str | None,
) -> str:
    api_key_line = f'\n    apiKey: "{api_key_env_var}",' if api_key_env_var else ""
    return f"""export default function (pi) {{
  pi.registerProvider("{provider_name}", {{
    baseUrl: {json.dumps(base_url)},{api_key_line}
    api: "openai-completions",
    models: [{{
      id: {json.dumps(model_name)},
      name: {json.dumps(model_name)},
      reasoning: true,
      input: ["text", "image"],
      cost: {{
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0
      }},
      contextWindow: 256000,
      maxTokens: 16384,
      compat: {{
        supportsReasoningEffort: true,
        reasoningEffortMap: {{
          minimal: "low",
          low: "low",
          medium: "medium",
          high: "high",
          xhigh: "high"
        }}
      }}
    }}]
  }});
}}
"""


def _build_workspace_extension(
    provider: ProviderSpec,
    provider_config: dict[str, str],
    model_name: str,
) -> tuple[str | None, str | None]:
    if provider.id == "openai" and provider_config.get("base_url"):
        return provider.pi_provider, _render_openai_override_extension(provider_config["base_url"])

    if provider.id == "ollama":
        base_url = provider_config.get("base_url") or provider.base_url or "http://localhost:11434/v1"
        return "syke-ollama", _render_openai_compatible_extension(
            "syke-ollama",
            base_url=base_url,
            model_name=model_name,
            api_key_env_var=None,
        )

    if provider.id == "vllm":
        base_url = provider_config.get("base_url")
        if not base_url:
            raise RuntimeError("Provider 'vllm' requires [providers.vllm].base_url or VLLM_API_BASE.")
        return "syke-vllm", _render_openai_compatible_extension(
            "syke-vllm",
            base_url=base_url,
            model_name=model_name,
            api_key_env_var="SYKE_PI_API_KEY",
        )

    if provider.id == "llama-cpp":
        base_url = provider_config.get("base_url")
        if not base_url:
            raise RuntimeError(
                "Provider 'llama-cpp' requires [providers.llama-cpp].base_url or LLAMA_CPP_API_BASE."
            )
        return "syke-llama-cpp", _render_openai_compatible_extension(
            "syke-llama-cpp",
            base_url=base_url,
            model_name=model_name,
            api_key_env_var="SYKE_PI_API_KEY",
        )

    if provider.id == "azure-ai":
        raise RuntimeError(
            "Provider 'azure-ai' is not yet mapped to a native Pi provider. "
            "Use openai/azure/openrouter/zai/kimi/codex, or configure Pi directly."
        )

    return provider.pi_provider, None


def configure_pi_workspace(
    workspace_root: Path,
    *,
    session_dir: Path | None = None,
    provider: ProviderSpec | None = None,
    model_override: str | None = None,
    thinking_budget: int | None = None,
) -> dict[str, str]:
    """Write project-local Pi settings and return env overrides for the Pi process."""
    provider = provider or resolve_provider()
    provider_config = _resolve_provider_config(provider)
    model_name = _resolve_model_name(provider_config, model_override)
    env = build_pi_runtime_env(provider)

    default_provider, extension_source = _build_workspace_extension(
        provider,
        provider_config,
        model_name,
    )

    pi_dir = workspace_root / ".pi"
    pi_dir.mkdir(parents=True, exist_ok=True)

    settings: dict[str, object] = {
        "defaultModel": model_name,
        "defaultThinkingLevel": _thinking_level_from_budget(thinking_budget or SYNC_THINKING),
        "quietStartup": True,
    }
    if default_provider:
        settings["defaultProvider"] = default_provider
    if session_dir is not None:
        settings["sessionDir"] = str(session_dir)

    if extension_source:
        extensions_dir = pi_dir / "extensions"
        extensions_dir.mkdir(exist_ok=True)
        extension_path = extensions_dir / "syke-provider.mjs"
        extension_path.write_text(extension_source, encoding="utf-8")
        settings["extensions"] = ["extensions"]

    settings_path = pi_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return env
