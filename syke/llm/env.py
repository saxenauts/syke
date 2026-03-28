"""Provider resolution and Pi runtime env building.

Syke no longer builds Claude SDK subprocess environments. This module now
resolves the active provider and translates Syke auth/config into the env vars
Pi expects for its native provider system.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from urllib.parse import urlsplit, urlunsplit

from syke.llm.providers import PROVIDERS, ProviderSpec

log = logging.getLogger(__name__)


def _get_auth_store():
    from syke.llm import AuthStore  # runtime import to avoid import cycles

    return AuthStore()


def resolve_provider(cli_provider: str | None = None) -> ProviderSpec:
    """Resolve which provider to use.

    Precedence: CLI flag > SYKE_PROVIDER env > auth.json active_provider > fail.
    """
    provider_id = cli_provider or os.getenv("SYKE_PROVIDER")

    if not provider_id:
        store = _get_auth_store()
        provider_id = store.get_active_provider()

    if not provider_id:
        raise RuntimeError(
            "No provider configured. Run `syke auth use <provider>` or "
            "`syke auth set <provider> ... --use`."
        )

    spec = PROVIDERS.get(provider_id)
    if spec is None:
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider {provider_id!r}. Valid providers: {valid}")
    return spec


def build_pi_runtime_env(provider: ProviderSpec | None = None) -> dict[str, str]:
    """Build env vars for the Pi subprocess."""
    provider = provider or resolve_provider()
    provider_config = _resolve_provider_config(provider)
    token = _resolve_token(provider)

    env: dict[str, str] = {}

    if provider.pi_api_key_env_var and token:
        env[provider.pi_api_key_env_var] = token

    if provider.id == "openai":
        base_url = provider_config.get("base_url")
        if base_url:
            env["OPENAI_BASE_URL"] = base_url

    if provider.id == "azure":
        endpoint = provider_config.get("endpoint") or provider_config.get("base_url")
        if endpoint:
            env["AZURE_OPENAI_BASE_URL"] = _normalize_azure_pi_base_url(endpoint)
        env["AZURE_OPENAI_API_VERSION"] = _normalize_azure_pi_api_version(
            provider_config.get("api_version")
        )

    if provider.id in {"vllm", "llama-cpp"} and token:
        # Custom Pi workspace providers use a neutral API key env name.
        env["SYKE_PI_API_KEY"] = token

    return env


def _normalize_azure_pi_base_url(endpoint: str) -> str:
    """Translate legacy Syke Azure config into Pi's Responses base URL shape."""
    split = urlsplit(endpoint.strip())
    path = split.path.rstrip("/")

    if "/deployments/" in path:
        raise RuntimeError(
            "Azure deployment URLs are not supported by Pi-native runtime. "
            "Configure the Azure resource endpoint instead."
        )

    if path.endswith("/responses"):
        path = path[: -len("/responses")]

    if path in {"", "/"}:
        path = "/openai/v1"
    elif path == "/openai":
        path = "/openai/v1"
    elif path == "/openai/v1":
        pass

    return urlunsplit((split.scheme, split.netloc, path, "", ""))


def _normalize_azure_pi_api_version(api_version: str | None) -> str:
    """Pi's Azure Responses runtime is standardized on the v1 API contract."""
    if api_version and api_version.strip().lower() == "v1":
        return "v1"
    return "v1"


def _resolve_token(provider: ProviderSpec) -> str | None:
    """Resolve auth token. Precedence: provider-specific env var > auth.json."""
    if provider.token_env_var:
        val = os.getenv(provider.token_env_var)
        if val:
            return val

    store = _get_auth_store()
    token = store.get_token(provider.id)
    if token:
        return token

    return None


def _resolve_provider_config(provider: ProviderSpec) -> dict[str, str]:
    """Resolve provider-specific config. Precedence: config.toml base > env var overrides."""
    from syke.config import CFG

    env_var_overrides = {
        "azure": {"AZURE_API_BASE": "endpoint", "AZURE_API_VERSION": "api_version"},
        "openai": {"OPENAI_BASE_URL": "base_url"},
        "ollama": {"OLLAMA_HOST": "base_url"},
        "vllm": {"VLLM_API_BASE": "base_url"},
        "llama-cpp": {"LLAMA_CPP_API_BASE": "base_url"},
    }

    base = dict(CFG.providers.get(provider.id, {}))
    for env_var, config_key in env_var_overrides.get(provider.id, {}).items():
        val = os.getenv(env_var)
        if val:
            base[config_key] = val
    return base


@dataclass(frozen=True)
class ProviderReadiness:
    provider_id: str
    ready: bool
    detail: str


def evaluate_provider_readiness(provider_id: str) -> ProviderReadiness:
    """Return whether a provider is ready to be marked active and why."""
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise ValueError("Unknown provider %r" % provider_id)

    if provider_id == "codex":
        return _codex_provider_readiness()

    if provider_id in _API_KEY_PROVIDERS:
        return _api_key_provider_readiness(provider_id)

    return _pi_provider_readiness(spec)


_API_KEY_PROVIDERS = {"openrouter", "zai", "kimi"}


def _codex_provider_readiness() -> ProviderReadiness:
    from syke.llm.codex_auth import read_codex_auth, refresh_codex_token

    creds = read_codex_auth(warn=False)
    if creds is None:
        return ProviderReadiness("codex", False, "Run 'codex login' first.")
    if creds.is_expired:
        if refresh_codex_token(creds):
            return ProviderReadiness("codex", True, "ChatGPT account (recommended)")
        return ProviderReadiness(
            "codex",
            False,
            "Codex token expired — run 'codex login' to refresh.",
        )
    return ProviderReadiness("codex", True, "ChatGPT account (recommended)")


def _api_key_provider_readiness(provider_id: str) -> ProviderReadiness:
    spec = PROVIDERS[provider_id]
    token = _resolve_token(spec)
    if token:
        return ProviderReadiness(provider_id, True, "API key configured")
    return ProviderReadiness(
        provider_id,
        False,
        "Enter an API key with 'syke auth set %s --use'." % provider_id,
    )


def _pi_provider_readiness(spec: ProviderSpec) -> ProviderReadiness:
    provider_id = spec.id
    config = _resolve_provider_config(spec)
    token = _resolve_token(spec)
    missing: list[str] = []

    if provider_id == "azure":
        if not (config.get("endpoint") or config.get("base_url")):
            missing.append("endpoint/base_url")
        if not config.get("model"):
            missing.append("model")
        if not token:
            missing.append("API key")
    elif provider_id == "openai":
        if not config.get("model"):
            missing.append("model")
        if not token:
            missing.append("API key")
    elif provider_id == "ollama":
        if not config.get("model"):
            missing.append("model")
    elif provider_id in {"vllm", "llama-cpp"}:
        if not config.get("model"):
            missing.append("model")
        if not config.get("base_url"):
            missing.append("base_url")
    elif token is None and spec.token_env_var:
        missing.append("API key")

    if missing:
        detail = (
            "Missing configuration: %s. Run 'syke auth set %s ... --use'." %
            (", ".join(missing), provider_id)
        )
        return ProviderReadiness(provider_id, False, detail)

    return ProviderReadiness(provider_id, True, "Pi runtime configured")
