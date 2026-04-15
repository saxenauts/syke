"""Persistent Pi agent runtime.

Syke treats Pi as the canonical agent runtime. This client manages a long-lived
Pi RPC subprocess, prepares the workspace-local Pi settings, and turns Pi's RPC
event stream into structured runtime results.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from syke.pi_state import build_pi_agent_env, get_default_model
from syke.runtime.pi_settings import configure_pi_workspace

logger = logging.getLogger(__name__)

_PI_THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})
# Give Pi a brief moment to emit retry state after a retryable agent_end.
_RETRY_SETTLEMENT_GRACE_SECONDS = 0.2
_SUBPROCESS_ENV_KEYS = (
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


@dataclass(frozen=True)
class PiLaunchBinding:
    provider: str | None
    model: str


@dataclass(frozen=True)
class PiProviderCatalogEntry:
    id: str
    models: tuple[str, ...]
    available_models: tuple[str, ...]
    default_model: str | None
    oauth: bool
    oauth_name: str | None = None
    requires_base_url: bool = False


def _get_active_provider_spec():
    try:
        from syke.llm.env import resolve_provider

        return resolve_provider()
    except Exception:
        return None


def _raw_pi_model_request(model_override: str | None = None) -> tuple[str, bool]:
    if model_override:
        return model_override, True

    provider = _get_active_provider_spec()
    provider_name = _pi_provider_name(provider)

    default_model = get_default_model()
    if default_model:
        return default_model, True

    if provider_name:
        provider_default = _load_pi_provider_default_model(provider_name)
        if provider_default:
            return provider_default, False
    raise RuntimeError(
        "No Pi model is configured. Set Pi defaultModel or choose a provider/model in `syke setup`."
    )


def _pi_provider_name(provider) -> str | None:
    if provider is None:
        return None
    provider_id = getattr(provider, "id", None)
    return provider_id if isinstance(provider_id, str) and provider_id else None


def _looks_like_pi_alias(model_id: str) -> bool:
    if model_id.endswith("-latest"):
        return True
    return not bool(re.search(r"-\d{8}$", model_id))


def _split_thinking_suffix(pattern: str) -> tuple[str, str | None]:
    last_colon = pattern.rfind(":")
    if last_colon == -1:
        return pattern, None
    suffix = pattern[last_colon + 1 :]
    if suffix in _PI_THINKING_LEVELS:
        return pattern[:last_colon], suffix
    return pattern, None


def _match_pi_model_pattern(
    provider_name: str, requested: str, model_ids: tuple[str, ...]
) -> str | None:
    lower_to_id = {model_id.lower(): model_id for model_id in model_ids}
    candidate = requested.strip()

    exact = lower_to_id.get(candidate.lower())
    if exact:
        return exact

    provider_prefix = f"{provider_name}/"
    if candidate.lower().startswith(provider_prefix.lower()):
        stripped = candidate[len(provider_prefix) :].strip()
        exact = lower_to_id.get(stripped.lower())
        if exact:
            return exact
        candidate = stripped

    base_candidate, thinking = _split_thinking_suffix(candidate)
    exact = lower_to_id.get(base_candidate.lower())
    if exact:
        return f"{exact}:{thinking}" if thinking else exact

    matches = [model_id for model_id in model_ids if base_candidate.lower() in model_id.lower()]
    if not matches:
        return None

    aliases = sorted(model_id for model_id in matches if _looks_like_pi_alias(model_id))
    resolved = aliases[-1] if aliases else sorted(matches)[-1]
    return f"{resolved}:{thinking}" if thinking else resolved


def _format_model_examples(model_ids: tuple[str, ...]) -> str:
    examples = sorted(model_ids)[:3]
    return ", ".join(repr(model_id) for model_id in examples)


def _run_pi_node_script(
    script: str,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    node_bin = ensure_node_binary()
    env = {**os.environ, **build_pi_agent_env(extra_env)}
    return subprocess.run(
        [str(node_bin), "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PI_LOCAL_PREFIX),
        env=env,
    )


def _benchmark_judge_rpc_script() -> str:
    return """
import { Type } from "@sinclair/typebox";
import {
  createAgentSessionFromServices,
  createAgentSessionRuntime,
  createAgentSessionServices,
  defineTool,
  runRpcMode,
  SessionManager,
} from "@mariozechner/pi-coding-agent";

const cwd = process.env.SYKE_RPC_CWD || process.cwd();
const agentDir = process.env.PI_CODING_AGENT_DIR;
const sessionDir = process.env.SYKE_RPC_SESSION_DIR || undefined;
const provider = process.env.SYKE_RPC_PROVIDER || undefined;
const modelSpec = process.env.SYKE_RPC_MODEL || undefined;
const THINKING_LEVELS = new Set(["off", "minimal", "low", "medium", "high", "xhigh"]);

function splitModelSpec(spec) {
  if (!spec) return { modelId: undefined, thinkingLevel: undefined };
  const idx = spec.lastIndexOf(":");
  if (idx === -1) return { modelId: spec, thinkingLevel: undefined };
  const suffix = spec.slice(idx + 1);
  if (!THINKING_LEVELS.has(suffix)) return { modelId: spec, thinkingLevel: undefined };
  return { modelId: spec.slice(0, idx), thinkingLevel: suffix };
}

const { modelId, thinkingLevel } = splitModelSpec(modelSpec);

const verdictTool = defineTool({
  name: "submit_judge_verdict",
  label: "Submit Judge Verdict",
  description: "Submit the final benchmark verdict in structured form.",
  parameters: Type.Object({
    factual_grounding: Type.Object({
      score: Type.Union([Type.Literal("strong"), Type.Literal("partial"), Type.Literal("missed")]),
      reasoning: Type.String(),
    }),
    continuity: Type.Object({
      score: Type.Union([Type.Literal("strong"), Type.Literal("partial"), Type.Literal("missed")]),
      reasoning: Type.String(),
    }),
    overall_verdict: Type.Union([
      Type.Literal("pass"),
      Type.Literal("partial"),
      Type.Literal("fail"),
    ]),
    summary: Type.String({ minLength: 1 }),
  }),
  execute: async (_toolCallId, params) => ({
    content: [{ type: "text", text: "judge verdict recorded" }],
    details: params,
  }),
});

const createRuntime = async ({ cwd, sessionManager, sessionStartEvent }) => {
  const services = await createAgentSessionServices({ cwd, agentDir });
  const selectedModel = provider && modelId
    ? services.modelRegistry.find(provider, modelId)
    : undefined;
  if (provider && modelId && !selectedModel) {
    throw new Error(`Model not found in registry: ${provider}/${modelId}`);
  }
  return {
    ...(await createAgentSessionFromServices({
      services,
      sessionManager,
      sessionStartEvent,
      model: selectedModel,
      thinkingLevel,
      customTools: [verdictTool],
    })),
    services,
    diagnostics: services.diagnostics,
  };
};

const runtime = await createAgentSessionRuntime(createRuntime, {
  cwd,
  agentDir,
  sessionManager: SessionManager.create(cwd, sessionDir),
});

await runRpcMode(runtime);
"""


def _build_rpc_launch_command(
    *,
    provider: str | None,
    model: str,
    runtime_profile: str | None,
    session_dir: Path,
    workspace_dir: Path,
) -> tuple[list[str], dict[str, str]]:
    if runtime_profile != "benchmark_judge":
        cmd = [
            resolve_pi_binary(),
            "--mode",
            "rpc",
        ]
        if provider:
            cmd.extend(["--provider", provider])
        cmd.extend(
            [
                "--model",
                model,
                "--session-dir",
                str(session_dir),
            ]
        )
        return cmd, {}

    node_bin = ensure_node_binary()
    extra_env = {
        "SYKE_RPC_CWD": str(workspace_dir),
        "SYKE_RPC_SESSION_DIR": str(session_dir),
        "SYKE_RPC_MODEL": model,
        "SYKE_PI_RUNTIME_PROFILE": "benchmark_judge",
    }
    if provider:
        extra_env["SYKE_RPC_PROVIDER"] = provider
    return [str(node_bin), "--input-type=module", "-e", _benchmark_judge_rpc_script()], extra_env


def _load_pi_catalog() -> tuple[PiProviderCatalogEntry, ...]:
    if not PI_PACKAGE_ROOT.exists():
        return ()

    script = """
import { AuthStorage, ModelRegistry } from "@mariozechner/pi-coding-agent";
import { getOAuthProviders } from "@mariozechner/pi-ai/oauth";
import { defaultModelPerProvider } from
  "./node_modules/@mariozechner/pi-coding-agent/dist/core/model-resolver.js";

const authStorage = AuthStorage.create();
const modelRegistry = ModelRegistry.create(authStorage);
const allModels = modelRegistry.getAll();
const availableModels = modelRegistry.getAvailable();
const oauthProviders = getOAuthProviders();
const oauthById = new Map(oauthProviders.map((provider) => [provider.id, provider]));
const availableByProvider = new Map();
for (const model of availableModels) {
  const current = availableByProvider.get(model.provider) ?? [];
  current.push(model.id);
  availableByProvider.set(model.provider, current);
}
const grouped = new Map();
for (const model of allModels) {
  const current = grouped.get(model.provider) ?? [];
  current.push(model.id);
  grouped.set(model.provider, current);
}
const payload = Array.from(grouped.entries())
  .sort((a, b) => a[0].localeCompare(b[0]))
  .map(([provider, modelIds]) => {
    const ids = [...new Set(modelIds)].sort();
    const providerModels = allModels.filter((model) => model.provider === provider);
    const preferred = defaultModelPerProvider[provider];
    const defaultModel = preferred && ids.includes(preferred) ? preferred : (ids[0] ?? null);
    const oauth = oauthById.get(provider);
    return {
      id: provider,
      models: ids,
      availableModels: [...new Set(availableByProvider.get(provider) ?? [])].sort(),
      defaultModel,
      oauth: Boolean(oauth),
      oauthName: oauth?.name ?? null,
      requiresBaseUrl: providerModels.some((model) => !String(model.baseUrl ?? "").trim())
    };
  });
process.stdout.write(JSON.stringify(payload));
"""
    try:
        result = _run_pi_node_script(script)
    except Exception:
        return ()

    if result.returncode != 0:
        logger.debug("Failed to query Pi catalog: %s", result.stderr.strip())
        return ()

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ()

    if not isinstance(raw, list):
        return ()

    entries: list[PiProviderCatalogEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("id")
        models = item.get("models")
        available = item.get("availableModels")
        if (
            not isinstance(provider_id, str)
            or not isinstance(models, list)
            or not isinstance(available, list)
        ):
            continue
        entries.append(
            PiProviderCatalogEntry(
                id=provider_id,
                models=tuple(model for model in models if isinstance(model, str) and model),
                available_models=tuple(
                    model for model in available if isinstance(model, str) and model
                ),
                default_model=item.get("defaultModel")
                if isinstance(item.get("defaultModel"), str)
                else None,
                oauth=bool(item.get("oauth")),
                oauth_name=item.get("oauthName")
                if isinstance(item.get("oauthName"), str)
                else None,
                requires_base_url=bool(item.get("requiresBaseUrl")),
            )
        )
    return tuple(entries)


def get_pi_provider_catalog() -> tuple[PiProviderCatalogEntry, ...]:
    return _load_pi_catalog()


def run_pi_oauth_login(provider_id: str, *, manual: bool = False) -> None:
    """Run Pi's native OAuth login flow for a provider."""
    script = """
import readline from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { AuthStorage } from "@mariozechner/pi-coding-agent";

const provider = process.env.SYKE_PI_LOGIN_PROVIDER;
const manual = process.env.SYKE_PI_LOGIN_MANUAL === "1";
if (!provider) {
  throw new Error("Missing SYKE_PI_LOGIN_PROVIDER");
}

const authStorage = AuthStorage.create();
const rl = readline.createInterface({ input: stdin, output: stdout });

try {
  const callbacks = {
    onAuth: (info) => {
      console.log(`Open this URL to continue: ${info.url}`);
      if (info.instructions) console.log(info.instructions);
    },
    onPrompt: async (prompt) => {
      const placeholder = prompt.placeholder ? ` (${prompt.placeholder})` : "";
      return await rl.question(`${prompt.message}${placeholder}: `);
    },
    onProgress: (message) => {
      console.log(message);
    }
  };

  if (manual) {
    callbacks.onManualCodeInput = async () => {
      return await rl.question("Paste the final redirect URL or authorization code: ");
    };
  }

  await authStorage.login(provider, callbacks);
} finally {
  rl.close();
}
"""
    result = subprocess.run(
        [str(ensure_node_binary()), "--input-type=module", "-e", script],
        text=True,
        cwd=str(PI_LOCAL_PREFIX),
        env={
            **os.environ,
            **build_pi_agent_env(
                {
                    "SYKE_PI_LOGIN_PROVIDER": provider_id,
                    "SYKE_PI_LOGIN_MANUAL": "1" if manual else "0",
                }
            ),
        },
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Pi login failed for {provider_id!r}")


def probe_pi_provider_connection(
    provider_id: str,
    model_id: str,
    *,
    timeout_seconds: int = 45,
    prompt: str = "Reply with only: ping",
) -> tuple[bool, str]:
    """Run a minimal non-tool Pi request to verify provider connectivity."""
    try:
        result = subprocess.run(
            [
                str(resolve_pi_binary()),
                "--provider",
                provider_id,
                "--model",
                model_id,
                "--no-tools",
                "-p",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(PI_LOCAL_PREFIX),
            env=_build_pi_process_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"probe timed out after {timeout_seconds}s"
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode == 0 and stdout:
        return True, stdout
    detail = stderr or stdout or f"exit {result.returncode}"
    return False, detail[:500]


def _load_pi_provider_default_model(provider_name: str) -> str | None:
    for entry in _load_pi_catalog():
        if entry.id == provider_name:
            return entry.default_model
    return None


def _load_pi_provider_model_ids(provider_name: str) -> tuple[str, ...]:
    for entry in _load_pi_catalog():
        if entry.id == provider_name:
            return entry.models
    return ()


def _resolve_pi_launch_binding_for_request(
    provider, requested_model: str, explicit_model: bool
) -> PiLaunchBinding:
    provider_name = _pi_provider_name(provider)

    if provider_name is None:
        return PiLaunchBinding(provider=None, model=requested_model)

    known_model_ids = _load_pi_provider_model_ids(provider_name)
    if not known_model_ids:
        return PiLaunchBinding(provider=provider_name, model=requested_model)

    resolved_model = _match_pi_model_pattern(provider_name, requested_model, known_model_ids)
    if resolved_model:
        return PiLaunchBinding(provider=provider_name, model=resolved_model)

    if explicit_model:
        return PiLaunchBinding(provider=provider_name, model=requested_model)

    example_text = _format_model_examples(known_model_ids)
    provider_id = getattr(provider, "id", provider_name)
    raise RuntimeError(
        f"Configured synthesis model {requested_model!r} is not a known Pi model for provider "
        f"{provider_name!r}. Set Pi defaultModel for {provider_id!r} to an exact Pi model ID"
        f" like {example_text}."
    )


def resolve_pi_launch_binding(model_override: str | None = None) -> PiLaunchBinding:
    provider = _get_active_provider_spec()
    requested_model, explicit_model = _raw_pi_model_request(model_override)
    return _resolve_pi_launch_binding_for_request(provider, requested_model, explicit_model)


def resolve_pi_model(model_override: str | None = None) -> str:
    """Resolve the Pi model from override -> config -> exact provider-scoped model."""
    return resolve_pi_launch_binding(model_override).model


def resolve_pi_provider(model_override: str | None = None) -> str | None:
    """Resolve the active Pi provider name for runtime launch."""
    return resolve_pi_launch_binding(model_override).provider


PI_PACKAGE = "@mariozechner/pi-coding-agent"
PI_LOCAL_PREFIX = Path.home() / ".syke" / "pi"
PI_BIN = Path.home() / ".syke" / "bin" / "pi"
PI_NODE_BIN = Path.home() / ".syke" / "bin" / "node"
PI_PACKAGE_ROOT = PI_LOCAL_PREFIX / "node_modules" / "@mariozechner" / "pi-coding-agent"
PI_CLI_JS = PI_PACKAGE_ROOT / "dist" / "cli.js"

_NODE_CANDIDATES = [
    Path("/opt/homebrew/bin/node"),
    Path("/usr/local/bin/node"),
    Path("/usr/bin/node"),
]
_NPM_CANDIDATES = [
    Path("/opt/homebrew/bin/npm"),
    Path("/usr/local/bin/npm"),
    Path("/usr/bin/npm"),
]


def _find_executable(name: str, candidates: list[Path]) -> Path | None:
    resolved = shutil.which(name)
    if resolved:
        path = Path(resolved).expanduser().resolve()
        if path.exists() and os.access(path, os.X_OK):
            return path

    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _ensure_symlink(link_path: Path, target_path: Path) -> Path:
    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.is_symlink():
        try:
            if link_path.resolve() == target_path.resolve() and os.access(link_path, os.X_OK):
                return link_path
        except OSError:
            pass
        link_path.unlink()
    elif link_path.exists():
        if link_path.resolve() == target_path.resolve() and os.access(link_path, os.X_OK):
            return link_path
        link_path.unlink()

    link_path.symlink_to(target_path)
    return link_path


def ensure_node_binary() -> Path:
    """Return a stable absolute Node path Syke can use outside shell-managed PATH."""
    if PI_NODE_BIN.exists() and os.access(PI_NODE_BIN, os.X_OK):
        return PI_NODE_BIN

    node = _find_executable("node", _NODE_CANDIDATES)
    if node is None:
        raise RuntimeError(
            "Syke's Pi runtime requires Node.js (>= 18). Install from https://nodejs.org"
        )
    return _ensure_symlink(PI_NODE_BIN, node)


def _resolve_npm_binary() -> str:
    npm = _find_executable("npm", _NPM_CANDIDATES)
    if npm is None:
        raise RuntimeError(
            "Syke's Pi runtime requires npm to install Pi locally. Install Node.js from "
            "https://nodejs.org"
        )
    return str(npm)


def _write_pi_launcher(node_bin: Path) -> Path:
    """Write the stable Pi launcher Syke uses for shell and daemon paths."""
    if not PI_CLI_JS.exists():
        raise RuntimeError(f"Pi CLI entrypoint not found at {PI_CLI_JS}")

    PI_BIN.parent.mkdir(parents=True, exist_ok=True)
    if PI_BIN.is_symlink():
        PI_BIN.unlink()
    elif PI_BIN.exists() and not PI_BIN.is_file():
        PI_BIN.unlink()
    launcher = f'#!/bin/sh\nexec "{node_bin}" "{PI_CLI_JS}" "$@"\n'
    PI_BIN.write_text(launcher, encoding="utf-8")
    PI_BIN.chmod(PI_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return PI_BIN


def ensure_pi_binary() -> str:
    """Install Pi locally under ~/.syke/ and return a stable launcher path."""
    node_bin = ensure_node_binary()

    if PI_BIN.exists() and os.access(PI_BIN, os.X_OK) and PI_CLI_JS.exists():
        _write_pi_launcher(node_bin)
        return str(PI_BIN)

    if PI_CLI_JS.exists():
        _write_pi_launcher(node_bin)
        return str(PI_BIN)

    npm = _resolve_npm_binary()

    logger.info("Installing Pi runtime to %s", PI_LOCAL_PREFIX)
    PI_LOCAL_PREFIX.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [npm, "install", "--prefix", str(PI_LOCAL_PREFIX), PI_PACKAGE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to install Pi runtime: {result.stderr.strip()[:500]}")

    if not PI_CLI_JS.exists():
        raise RuntimeError(f"Pi CLI entrypoint not found after install at {PI_CLI_JS}")

    _write_pi_launcher(node_bin)
    logger.info("Pi runtime installed: %s -> node=%s cli=%s", PI_BIN, node_bin, PI_CLI_JS)
    return str(PI_BIN)


def resolve_pi_binary() -> str:
    """Find or install the Pi binary at ~/.syke/bin/pi."""
    return ensure_pi_binary()


def get_pi_version(*, install: bool = False, minimal_env: bool = False, timeout: int = 10) -> str:
    """Return Pi version through Syke's stable launcher.

    When ``minimal_env`` is true, simulate a launchd-style cold environment with
    a stripped PATH to catch shell-dependent runtime failures.
    """
    launcher = Path(ensure_pi_binary() if install else PI_BIN)
    if not launcher.exists():
        raise FileNotFoundError(f"Pi launcher not found at {launcher}")

    env: dict[str, str] | None = None
    if minimal_env:
        env = {
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }

    result = subprocess.run(
        [str(launcher), "--version"],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(detail[:500])
    return result.stdout.strip() or result.stderr.strip() or "unknown"


def _build_subprocess_env(runtime_env: dict[str, str]) -> dict[str, str]:
    """Build a bounded child env for Pi instead of inheriting the full host shell."""
    env: dict[str, str] = {}
    for key in _SUBPROCESS_ENV_KEYS:
        value = os.getenv(key)
        if value:
            env[key] = value
    for key, value in os.environ.items():
        if not value:
            continue
        if (
            key.startswith("PI_")
            or key.startswith("AWS_")
            or key.startswith("GOOGLE_")
            or key in {"HF_TOKEN"}
            or key.endswith("_API_KEY")
            or key.endswith("_TOKEN")
            or key.endswith("_BASE_URL")
            or key.endswith("_RESOURCE_NAME")
            or key.endswith("_API_VERSION")
            or key.endswith("_DEPLOYMENT_NAME_MAP")
        ):
            env[key] = value
    env.update(runtime_env)
    return env


def _build_pi_process_env(runtime_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build the exact Pi child-process env used by both probe and runtime launch."""
    return _build_subprocess_env(runtime_env or build_pi_agent_env())


def _extract_assistant_message(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type in {"message", "message_start", "message_end", "turn_end"}:
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message
        return None

    if event_type == "agent_end":
        messages = event.get("messages")
        if isinstance(messages, list):
            for candidate in reversed(messages):
                if isinstance(candidate, dict) and candidate.get("role") == "assistant":
                    return candidate
        return None

    if event_type != "message_update":
        return None

    message = event.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant":
        return message

    inner = _extract_message_update_event(event)
    if not isinstance(inner, dict):
        return None
    for key in ("message", "partial"):
        candidate = inner.get(key)
        if isinstance(candidate, dict) and candidate.get("role") == "assistant":
            return candidate
    return None


def _extract_message_update_event(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "message_update":
        return None
    for key in ("assistantMessageEvent", "event"):
        inner = event.get(key)
        if isinstance(inner, dict):
            return inner
    return None


def _extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
        elif block_type in {"thinking", "reasoning"} and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    return "".join(chunks)


def _extract_usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int):
            return value
    return None


def _is_retryable_pi_error(error_message: str) -> bool:
    return bool(
        re.search(
            r"overloaded|provider.?returned.?error|rate.?limit"
            r"|too many requests|429|500|502|503|504"
            r"|service.?unavailable|server.?error|internal.?error"
            r"|network.?error|connection.?error|connection.?refused"
            r"|other side closed|fetch failed|upstream.?connect"
            r"|reset before headers|socket hang up"
            r"|timed? out|timeout|terminated|retry delay",
            error_message,
            re.IGNORECASE,
        )
    )


def _extract_tool_invocation(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("type")
    if event_type in {"tool_execution_start", "tool_call"}:
        tool = event.get("toolExecution")
        if not isinstance(tool, dict):
            tool = event.get("toolCall")
        if not isinstance(tool, dict):
            return None
        name = tool.get("name") or tool.get("toolName") or "tool"
        return {
            "name": str(name),
            "input": tool.get("input"),
            "id": tool.get("id"),
        }

    inner = _extract_message_update_event(event)
    if not isinstance(inner, dict) or inner.get("type") != "toolcall_start":
        return None

    tool = inner.get("toolCall")
    if not isinstance(tool, dict):
        return None
    name = tool.get("toolName") or tool.get("name") or "tool"
    return {
        "name": str(name),
        "input": tool.get("input") or tool.get("arguments"),
        "id": tool.get("id"),
    }


def _extract_tool_invocations_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    if message.get("role") != "assistant":
        return []

    content = message.get("content")
    if not isinstance(content, list):
        return []

    invocations: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "toolCall":
            continue
        name = block.get("name") or block.get("toolName") or "tool"
        invocations.append(
            {
                "name": str(name),
                "input": block.get("arguments") or block.get("input"),
                "id": block.get("id"),
            }
        )
    return invocations


def _dedupe_tool_invocations(invocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for invocation in invocations:
        key = str(invocation.get("id") or json.dumps(invocation, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(invocation)
    return deduped


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def build_transcript(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []

    for event in events:
        if event.get("type") != "message":
            continue

        message = event.get("message")
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type in {"thinking", "reasoning"}:
                        thinking_text = block.get("text") or block.get("thinking")
                        if isinstance(thinking_text, str) and thinking_text:
                            blocks.append({"type": "thinking", "text": thinking_text})
                    elif block_type == "text":
                        text = block.get("text")
                        if isinstance(text, str) and text:
                            blocks.append({"type": "text", "text": text})
                    elif block_type == "toolCall":
                        raw_input = block.get("arguments") or block.get("input") or {}
                        tool_input = raw_input if isinstance(raw_input, dict) else {}
                        blocks.append(
                            {
                                "type": "tool_use",
                                "name": str(block.get("name") or block.get("toolName") or "tool"),
                                "input": dict(tool_input),
                            }
                        )
            if blocks:
                transcript.append({"role": "assistant", "blocks": blocks})
            continue

        if role == "toolResult":
            tool_name = message.get("toolName")
            content_text = _message_content_to_text(message.get("content"))
            transcript.append(
                {
                    "role": "user",
                    "blocks": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.get("toolCallId"),
                            "tool_name": str(tool_name) if tool_name is not None else None,
                            "content": content_text,
                            "is_error": bool(message.get("isError", False)),
                        }
                    ],
                }
            )
            continue

        if role == "user":
            text = _message_content_to_text(message.get("content"))
            if text:
                transcript.append({"role": "user", "blocks": [{"type": "text", "text": text}]})

    return transcript


def build_transcript_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return build_transcript(
        [
            {"type": "message", "message": message}
            for message in messages
            if isinstance(message, dict)
        ]
    )


class RpcEventStream:
    """Threaded reader for Pi's JSONL RPC stream."""

    def __init__(self, stdout):
        self._stdout = stdout
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._error: str | None = None
        self._last_reset_at = time.monotonic()
        self._callback: Callable[[dict[str, Any]], None] | None = None

    def start(self) -> None:
        self._thread.start()

    def set_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        with self._lock:
            self._callback = callback

    def _read_loop(self) -> None:
        try:
            for line in self._stdout:
                received_at = time.monotonic()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Non-JSON line from Pi: %s", line[:200])
                    continue

                callback: Callable[[dict[str, Any]], None] | None = None
                with self._lock:
                    if received_at < self._last_reset_at:
                        continue
                    self._events.append(event)
                    callback = self._callback

                    event_type = event.get("type", "")
                    if event_type == "agent_end":
                        self._done.set()
                    elif event_type == "error":
                        self._error = event.get("message", "Unknown Pi error")
                    elif event_type == "response" and event.get("success") is False:
                        self._error = event.get("error", "Pi command failed")

                if callback is not None:
                    try:
                        callback(event)
                    except Exception:
                        logger.debug("Pi event callback failed", exc_info=True)
        except Exception as exc:
            self._error = str(exc)
            self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout=timeout)

    def reset(self) -> None:
        time.sleep(0.1)
        with self._lock:
            self._events.clear()
            self._done.clear()
            self._error = None
            self._last_reset_at = time.monotonic()

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    @property
    def error(self) -> str | None:
        return self._error

    def get_output(self) -> str:
        text_deltas: list[str] = []
        final_text: str | None = None

        for event in self.events:
            if event.get("type") == "text":
                content = event.get("content")
                if isinstance(content, str):
                    text_deltas.append(content)
                continue

            inner = _extract_message_update_event(event)
            if not isinstance(inner, dict):
                continue

            if inner.get("type") == "text_delta":
                delta = inner.get("delta")
                if isinstance(delta, str):
                    text_deltas.append(delta)

            message = _extract_assistant_message(event)
            if not isinstance(message, dict):
                continue
            message_text = _extract_message_text(message)
            if message_text:
                final_text = message_text

        if final_text:
            return final_text.strip()
        if text_deltas:
            return "".join(text_deltas).strip()
        return ""

    def get_thinking_chunks(self) -> list[str]:
        chunks: list[str] = []
        for event in self.events:
            inner = _extract_message_update_event(event)
            if not isinstance(inner, dict):
                continue
            if inner.get("type") == "thinking_delta":
                delta = inner.get("delta")
                if isinstance(delta, str) and delta:
                    chunks.append(delta)
        return chunks

    def get_tool_calls(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for event in self.events:
            event_type = event.get("type")
            if event_type in {"tool_call", "tool_execution_start"}:
                calls.append(event)
                continue
            if event_type == "message":
                message = event.get("message")
                if isinstance(message, dict):
                    for invocation in _extract_tool_invocations_from_message(message):
                        calls.append(
                            {
                                "type": "tool_call",
                                "toolCall": {
                                    "id": invocation.get("id"),
                                    "name": invocation.get("name"),
                                    "input": invocation.get("input"),
                                },
                            }
                        )
                continue
            inner = _extract_message_update_event(event)
            if not isinstance(inner, dict):
                continue
            if inner.get("type") in {"toolcall_start", "toolcall_end"}:
                calls.append(event)
        return calls

    def get_tool_invocations(self) -> list[dict[str, Any]]:
        invocations: list[dict[str, Any]] = []
        for event in self.events:
            if event.get("type") == "message":
                message = event.get("message")
                if isinstance(message, dict):
                    invocations.extend(_extract_tool_invocations_from_message(message))
            invocation = _extract_tool_invocation(event)
            if invocation is not None:
                invocations.append(invocation)
        return _dedupe_tool_invocations(invocations)

    def get_usage(self) -> dict[str, int | float | None]:
        latest_message: dict[str, Any] | None = None
        for event in self.events:
            message = _extract_assistant_message(event)
            if isinstance(message, dict):
                latest_message = message

        if latest_message is None:
            return {
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "cost_usd": None,
            }

        usage = latest_message.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        cost = latest_message.get("cost")
        if not isinstance(cost, dict):
            cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}

        return {
            "input_tokens": _extract_usage_int(usage, "input_tokens", "input"),
            "output_tokens": _extract_usage_int(usage, "output_tokens", "output"),
            "cache_read_tokens": _extract_usage_int(usage, "cache_read_tokens", "cacheRead"),
            "cache_write_tokens": _extract_usage_int(usage, "cache_write_tokens", "cacheWrite"),
            "cost_usd": cost.get("total") if isinstance(cost.get("total"), (int, float)) else None,
        }

    def get_assistant_error(self) -> str | None:
        latest_message: dict[str, Any] | None = None
        for event in self.events:
            message = _extract_assistant_message(event)
            if isinstance(message, dict):
                latest_message = message
        if latest_message is None:
            return None
        if latest_message.get("stopReason") == "error":
            error_message = latest_message.get("errorMessage")
            if isinstance(error_message, str) and error_message:
                return error_message
            return "Pi assistant message ended with stopReason=error"
        return None

    def get_message_metadata(self) -> dict[str, str | None]:
        latest_message: dict[str, Any] | None = None
        for event in self.events:
            message = _extract_assistant_message(event)
            if isinstance(message, dict):
                latest_message = message

        if latest_message is None:
            return {"provider": None, "model": None, "response_id": None, "stop_reason": None}

        provider = latest_message.get("provider")
        model = latest_message.get("model")
        response_id = latest_message.get("responseId")
        stop_reason = latest_message.get("stopReason")
        return {
            "provider": provider if isinstance(provider, str) else None,
            "model": model if isinstance(model, str) else None,
            "response_id": response_id if isinstance(response_id, str) else None,
            "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
        }

    def has_retry_in_progress(self) -> bool:
        last_retry_start = -1
        last_retry_end = -1
        for index, event in enumerate(self.events):
            event_type = event.get("type")
            if event_type == "auto_retry_start":
                last_retry_start = index
            elif event_type == "auto_retry_end":
                last_retry_end = index
        return last_retry_start > last_retry_end

    def latest_retry_terminal_error(self) -> str | None:
        last_retry_end: dict[str, Any] | None = None
        for event in self.events:
            if event.get("type") == "auto_retry_end":
                last_retry_end = event
        if last_retry_end is None:
            return None
        if last_retry_end.get("success") is True:
            return None
        final_error = last_retry_end.get("finalError")
        if isinstance(final_error, str) and final_error:
            return final_error
        return "Pi auto-retry failed"

    def latest_agent_end_is_retryable_error(self) -> bool:
        last_agent_end: dict[str, Any] | None = None
        for event in self.events:
            if event.get("type") == "agent_end":
                last_agent_end = event
        if last_agent_end is None:
            return False
        messages = last_agent_end.get("messages")
        if not isinstance(messages, list):
            return False
        last_assistant: dict[str, Any] | None = None
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "assistant":
                last_assistant = message
        if last_assistant is None:
            return False
        if last_assistant.get("stopReason") != "error":
            return False
        error_message = last_assistant.get("errorMessage")
        if not isinstance(error_message, str) or not error_message:
            return False
        return _is_retryable_pi_error(error_message)

    def wait_for_terminal_state(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            if remaining == 0.0:
                return False
            completed = self.wait(timeout=remaining)
            if not completed:
                return False
            if self.latest_retry_terminal_error() is not None:
                return True
            if self.has_retry_in_progress():
                self._done.clear()
                continue
            if self.latest_agent_end_is_retryable_error():
                grace = _RETRY_SETTLEMENT_GRACE_SECONDS
                if deadline is not None:
                    grace = min(grace, max(deadline - time.monotonic(), 0.0))
                if grace > 0:
                    time.sleep(grace)
                if self.latest_retry_terminal_error() is not None:
                    return True
                if self.has_retry_in_progress():
                    self._done.clear()
                    continue
                return True
            return True


class _StderrDrain:
    """Threaded stderr reader to prevent Pi from blocking on a full pipe."""

    def __init__(self, stderr):
        self._stderr = stderr
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _read_loop(self) -> None:
        try:
            for line in self._stderr:
                line = line.rstrip()
                if not line:
                    continue
                with self._lock:
                    self._lines.append(line)
                logger.debug("Pi stderr: %s", line)
        except Exception as exc:
            logger.debug("Pi stderr drain stopped: %s", exc)

    def get_output(self) -> str:
        with self._lock:
            return "\n".join(self._lines)


class PiRuntime:
    """Persistent Pi agent runtime."""

    def __init__(
        self,
        workspace_dir: str | Path,
        session_dir: str | Path | None = None,
        model: str | None = None,
        runtime_profile: str | None = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        self.session_dir = Path(session_dir) if session_dir else self.workspace_dir / "sessions"
        self._model_override = model
        self.runtime_profile = runtime_profile
        self._binding_error: str | None = None
        try:
            binding = resolve_pi_launch_binding(model)
        except RuntimeError as exc:
            self._binding_error = str(exc)
            provider = _get_active_provider_spec()
            self.provider = _pi_provider_name(provider)
            self.model = _raw_pi_model_request(model)[0]
        else:
            self.provider = binding.provider
            self.model = binding.model
        self._process: subprocess.Popen[str] | None = None
        self._stream: RpcEventStream | None = None
        self._stderr_drain: _StderrDrain | None = None
        self._started_at: float | None = None
        self._last_start_duration_ms: int | None = None
        self._start_count = 0
        self._request_id = 0
        self._request_lock = threading.Lock()
        self._prompt_lock = threading.Lock()

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Start the Pi process in RPC mode."""
        if self.is_alive:
            logger.info("Pi runtime already alive")
            return
        started = time.monotonic()

        binding = resolve_pi_launch_binding(self._model_override)
        self._binding_error = None
        self.provider = binding.provider
        self.model = binding.model
        runtime_env = configure_pi_workspace(
            self.workspace_dir,
            session_dir=self.session_dir,
            model_override=self.model,
        )

        cmd, extra_env = _build_rpc_launch_command(
            provider=self.provider,
            model=self.model,
            runtime_profile=self.runtime_profile,
            session_dir=self.session_dir,
            workspace_dir=self.workspace_dir,
        )

        logger.info(
            "Starting runtime: %s/%s%s",
            self.provider or "auto",
            self.model,
            f" [{self.runtime_profile}]" if self.runtime_profile else "",
        )

        # Wrap with OS sandbox if available
        from syke.runtime.sandbox import sandbox_available, wrap_command, write_sandbox_profile

        sandbox_profile = None
        if sandbox_available() and not os.environ.get("SYKE_DISABLE_SANDBOX"):
            sandbox_profile = write_sandbox_profile(self.workspace_dir)
            if sandbox_profile:
                cmd = wrap_command(cmd, sandbox_profile)
                logger.info("Pi launching inside OS sandbox")

        logger.debug("Pi runtime command: %s", " ".join(cmd))

        env = _build_pi_process_env({**runtime_env, **extra_env})
        launch_cwd = str(PI_LOCAL_PREFIX) if self.runtime_profile == "benchmark_judge" else str(self.workspace_dir)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=launch_cwd,
            env=env,
            bufsize=1,
            text=True,
        )

        if self._process.stdout is None or self._process.stderr is None:
            raise RuntimeError("Pi failed to expose stdio pipes")

        self._stream = RpcEventStream(self._process.stdout)
        self._stderr_drain = _StderrDrain(self._process.stderr)
        self._stream.start()
        self._stderr_drain.start()
        self._started_at = time.time()

        time.sleep(1.0)
        if not self.is_alive:
            stderr = self._stderr_drain.get_output() if self._stderr_drain else ""
            raise RuntimeError(f"Pi failed to start: {stderr[:500]}")

        self._last_start_duration_ms = int((time.monotonic() - started) * 1000)
        self._start_count += 1
        logger.debug("Pi runtime started (pid=%s)", self._process.pid)

    def stop(self) -> None:
        """Stop the Pi process gracefully."""
        with self._prompt_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self._process is None:
            return

        pid = self._process.pid
        logger.info("Stopping Pi runtime (pid=%s)", pid)
        try:
            self._send({"type": "command", "command": "/quit"})
            self._process.wait(timeout=5)
        except (subprocess.TimeoutExpired, BrokenPipeError, OSError):
            logger.warning("Pi did not quit gracefully, terminating")
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()

        self._process = None
        self._stream = None
        self._stderr_drain = None
        logger.debug("Pi runtime stopped (was pid=%s)", pid)

    def new_session(
        self,
        *,
        parent_session: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Start a fresh Pi session while reusing the warm runtime process."""
        command: dict[str, Any] = {"type": "new_session"}
        if parent_session:
            command["parentSession"] = parent_session
        response = self._send_request(command, timeout=timeout)
        return response if isinstance(response, dict) else {}

    def get_session_stats(self, *, timeout: float = 10.0) -> dict[str, Any]:
        """Fetch Pi's current per-session stats."""
        response = self._send_request({"type": "get_session_stats"}, timeout=timeout)
        return response if isinstance(response, dict) else {}

    def get_messages(self, *, timeout: float = 10.0) -> list[dict[str, Any]]:
        """Fetch all messages for the current Pi session."""
        response = self._send_request({"type": "get_messages"}, timeout=timeout)
        messages = response.get("messages")
        if not isinstance(messages, list):
            return []
        return [message for message in messages if isinstance(message, dict)]

    def prompt(
        self,
        text: str,
        *,
        timeout: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        new_session: bool = False,
    ) -> PiCycleResult:
        """Send a prompt to Pi and wait for completion."""
        with self._prompt_lock:
            if not self.is_alive or self._stream is None:
                raise RuntimeError("Pi runtime is not running")

            if new_session:
                self._stream.set_callback(None)
                self._stream.reset()
                self.new_session(timeout=min(timeout or 30.0, 30.0))

            self._stream.set_callback(on_event)
            self._stream.reset()

            self._send({"type": "prompt", "message": text})
            start = time.time()
            wait_for_terminal_state = getattr(self._stream, "wait_for_terminal_state", None)
            if callable(wait_for_terminal_state):
                completed = wait_for_terminal_state(timeout=timeout)
            else:
                completed = self._stream.wait(timeout=timeout)
            duration_ms = int((time.time() - start) * 1000)

            events = self._stream.events
            usage = self._stream.get_usage()
            message_metadata = self._stream.get_message_metadata()
            assistant_error = self._stream.get_assistant_error()
            provider = message_metadata.get("provider")
            response_model = message_metadata.get("model")
            response_id = message_metadata.get("response_id")
            stop_reason = message_metadata.get("stop_reason")
            if not completed:
                timeout_error = (
                    self._stream.error
                    or assistant_error
                    or f"Pi did not complete within {timeout}s"
                )
                result = PiCycleResult(
                    status="timeout",
                    output=self._stream.get_output(),
                    thinking=self._stream.get_thinking_chunks(),
                    tool_calls=_dedupe_tool_invocations(self._stream.get_tool_invocations()),
                    events=events,
                    transcript=[],
                    num_turns=0,
                    duration_ms=duration_ms,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    cache_read_tokens=usage["cache_read_tokens"],
                    cache_write_tokens=usage["cache_write_tokens"],
                    cost_usd=usage["cost_usd"],
                    provider=provider,
                    response_model=response_model,
                    response_id=response_id,
                    stop_reason=stop_reason,
                    error=timeout_error,
                )
                self._stream.set_callback(None)
                self._stop_locked()
                return result

            session_stats = self.get_session_stats(timeout=min(timeout or 10.0, 10.0))
            session_messages = self.get_messages(timeout=min(timeout or 10.0, 10.0))
            transcript = build_transcript_from_messages(session_messages)
            tool_calls: list[dict[str, Any]] = []
            for message in session_messages:
                tool_calls.extend(_extract_tool_invocations_from_message(message))
            tool_calls.extend(self._stream.get_tool_invocations())
            tool_calls = _dedupe_tool_invocations(tool_calls)
            assistant_messages = session_stats.get("assistantMessages")
            transcript_turns = sum(1 for item in transcript if item.get("role") == "assistant")
            if isinstance(assistant_messages, int) and assistant_messages > 0:
                num_turns = assistant_messages
            else:
                num_turns = transcript_turns
            result = PiCycleResult(
                status="completed"
                if completed and not self._stream.error and not assistant_error
                else "error",
                output=self._stream.get_output(),
                thinking=self._stream.get_thinking_chunks(),
                tool_calls=tool_calls,
                events=events,
                transcript=transcript,
                num_turns=num_turns,
                duration_ms=duration_ms,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                cost_usd=usage["cost_usd"],
                provider=provider,
                response_model=response_model,
                response_id=response_id,
                stop_reason=stop_reason,
                error=self._stream.error or assistant_error,
            )
            self._stream.set_callback(None)
            return result

    def _send(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Pi process not available")
        try:
            line = json.dumps(message) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError(f"Failed to send to Pi: {exc}") from exc

    def _send_request(self, command: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        if not self.is_alive or self._stream is None:
            raise RuntimeError("Pi runtime is not running")

        with self._request_lock:
            self._request_id += 1
            request_id = f"req_{self._request_id}"

        self._send({**command, "id": request_id})

        deadline = time.monotonic() + max(timeout, 0.1)
        scanned = 0
        while time.monotonic() < deadline:
            events = self._stream.events
            for event in events[scanned:]:
                if event.get("type") != "response" or event.get("id") != request_id:
                    continue
                if event.get("success") is False:
                    error = event.get("error") or f"Pi request failed: {command.get('type')}"
                    raise RuntimeError(str(error))
                data = event.get("data")
                return data if isinstance(data, dict) else {}
            scanned = len(events)
            if not self.is_alive:
                raise RuntimeError("Pi runtime exited while waiting for RPC response")
            time.sleep(0.01)

        raise TimeoutError(f"Timed out waiting for Pi RPC response to {command.get('type')}")

    @property
    def uptime_seconds(self) -> float | None:
        if self._started_at and self.is_alive:
            return time.time() - self._started_at
        return None

    def status(self) -> dict[str, Any]:
        session_count = 0
        if self.session_dir.exists():
            session_count = len(list(self.session_dir.glob("*.jsonl")))
        return {
            "alive": self.is_alive,
            "provider": self.provider,
            "model": self.model,
            "binding_error": self._binding_error,
            "workspace": str(self.workspace_dir),
            "session_dir": str(self.session_dir),
            "pid": self._process.pid if self._process else None,
            "uptime_s": self.uptime_seconds,
            "last_start_ms": self._last_start_duration_ms,
            "start_count": self._start_count,
            "session_count": session_count,
        }


class PiCycleResult:
    """Result of a single Pi prompt/response cycle."""

    def __init__(
        self,
        status: str,
        output: str,
        thinking: list[str],
        tool_calls: list[dict[str, Any]],
        events: list[dict[str, Any]],
        transcript: list[dict[str, Any]],
        num_turns: int,
        duration_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        cost_usd: float | None,
        provider: str | None,
        response_model: str | None,
        response_id: str | None,
        stop_reason: str | None,
        error: str | None = None,
    ):
        self.status = status
        self.output = output
        self.thinking = thinking
        self.tool_calls = tool_calls
        self.events = events
        self.transcript = transcript
        self.num_turns = num_turns
        self.duration_ms = duration_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens
        self.cost_usd = cost_usd
        self.provider = provider
        self.response_model = response_model
        self.response_id = response_id
        self.stop_reason = stop_reason
        self.error = error

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def __repr__(self) -> str:
        return (
            f"PiCycleResult(status={self.status!r}, output_len={len(self.output)}, "
            f"tool_calls={len(self.tool_calls)}, duration_ms={self.duration_ms})"
        )


PiClient = PiRuntime
