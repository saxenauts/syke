"""Bounded environment policy for Syke-owned child runtimes."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

BASE_CHILD_ENV_KEYS = (
    "HOME",
    "PATH",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "SHELL",
)
TEMP_ENV_KEYS = ("TMPDIR", "TMP", "TEMP")
PI_PASSTHROUGH_ENV_VAR = "SYKE_PI_PASSTHROUGH_ENV"
PI_TMPDIR_ENV_VAR = "SYKE_PI_TMPDIR"

ALWAYS_ALLOWED_HOST_ENV_KEYS = frozenset(
    {
        "PI_CODING_AGENT_DIR",
        # Benchmark/replay callers can pass an explicit rubric schema path into
        # the Pi RPC script. It is inert outside benchmark judge mode.
        "SYKE_RPC_RUBRIC_SPEC_PATH",
    }
)

PROVIDER_HOST_ENV_ALLOWLIST: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}),
    "openai": frozenset({"OPENAI_API_KEY", "OPENAI_BASE_URL"}),
    "openai-codex": frozenset({"OPENAI_API_KEY", "OPENAI_BASE_URL"}),
    "openrouter": frozenset({"OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"}),
    "azure-openai-responses": frozenset(
        {
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_BASE_URL",
            "AZURE_OPENAI_API_VERSION",
            "AZURE_OPENAI_RESOURCE_NAME",
            "AZURE_OPENAI_DEPLOYMENT_NAME_MAP",
        }
    ),
    "azure-anthropic-foundry": frozenset(
        {
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_BASE_URL",
        }
    ),
    "kimi-coding": frozenset({"KIMI_API_KEY", "MOONSHOT_API_KEY", "KIMI_BASE_URL"}),
    "zai": frozenset({"ZAI_API_KEY", "ZAI_BASE_URL"}),
}


def host_env_passthrough_keys(
    provider: str | None = None,
    *,
    host_env: Mapping[str, str] | None = None,
) -> set[str]:
    keys: set[str] = set(ALWAYS_ALLOWED_HOST_ENV_KEYS)
    if provider:
        keys.update(PROVIDER_HOST_ENV_ALLOWLIST.get(provider, frozenset()))

    source = host_env or os.environ
    extra = source.get(PI_PASSTHROUGH_ENV_VAR, "")
    for raw in re.split(r"[,\s]+", extra):
        key = raw.strip()
        if not key:
            continue
        if re.fullmatch(r"[A-Z0-9_]+", key):
            keys.add(key)
    return keys


def valid_temp_dir(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    try:
        if path.is_dir():
            return str(path)
    except OSError:
        return None
    return None


def darwin_user_temp_dir() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["getconf", "DARWIN_USER_TEMP_DIR"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return valid_temp_dir(result.stdout.strip())


def resolve_child_temp_dir(host_env: Mapping[str, str] | None = None) -> str | None:
    source = host_env or os.environ
    explicit = valid_temp_dir(source.get(PI_TMPDIR_ENV_VAR))
    if explicit:
        return explicit

    darwin_temp = darwin_user_temp_dir()
    if darwin_temp:
        return darwin_temp

    for key in TEMP_ENV_KEYS:
        temp_dir = valid_temp_dir(source.get(key))
        if temp_dir:
            return temp_dir
    return valid_temp_dir(tempfile.gettempdir())


def normalized_temp_env(host_env: Mapping[str, str] | None = None) -> dict[str, str]:
    temp_dir = resolve_child_temp_dir(host_env)
    if not temp_dir:
        return {}
    return {key: temp_dir for key in TEMP_ENV_KEYS}


def temp_paths_from_env(env: Mapping[str, str]) -> tuple[str, ...]:
    paths: list[str] = []
    for key in TEMP_ENV_KEYS:
        path = valid_temp_dir(env.get(key))
        if path:
            paths.append(path)
    return tuple(dict.fromkeys(paths))


def child_temp_paths(
    *,
    extra_temp_dirs: tuple[str, ...] | None = None,
    host_env: Mapping[str, str] | None = None,
) -> list[str]:
    paths = [
        *temp_paths_from_env(normalized_temp_env(host_env)),
        *(extra_temp_dirs or ()),
    ]
    resolved: list[str] = []
    for raw in paths:
        path = valid_temp_dir(raw)
        if path:
            resolved.append(path)
    return list(dict.fromkeys(resolved))


def build_child_process_env(
    runtime_env: Mapping[str, str] | None = None,
    *,
    provider: str | None = None,
    host_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a bounded child env instead of inheriting the full host shell."""
    source = host_env or os.environ
    env: dict[str, str] = {}
    for key in BASE_CHILD_ENV_KEYS:
        value = source.get(key)
        if value:
            env[key] = value
    for key in host_env_passthrough_keys(provider, host_env=source):
        value = source.get(key)
        if value:
            env[key] = value
    if runtime_env:
        env.update(runtime_env)
    env.update(normalized_temp_env(source))
    return env
