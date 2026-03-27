"""Claude backend common utilities — shared helpers for Claude Agent SDK operations.

This module contains utilities used by both synthesis and ask_agent that are
specific to the Claude backend implementation but shared across operations.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Skill file constants
_SKILL_DIR = Path(__file__).resolve().parent / "skills"
_SKILL_FILE = _SKILL_DIR / "synthesis.md"
_FALLBACK_PROMPT = "You are Syke's synthesis agent. Create and manage memories from new events. Call commit_cycle when done."


def _compute_real_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float | None:
    """Compute actual cost from LiteLLM's cost map instead of trusting the SDK.

    The Claude Agent SDK prices tokens using Anthropic's rates regardless of
    which model the LiteLLM proxy actually routes to. This function uses the
    proxy's actual model to look up the correct per-token rates.

    Returns None if the model isn't in the cost map (falls back to SDK cost).
    """
    try:
        import litellm

        entry = litellm.model_cost.get(model)
        if not entry:
            return None

        input_rate = entry.get("input_cost_per_token", 0)
        output_rate = entry.get("output_cost_per_token", 0)
        cache_rate = entry.get("cache_read_input_token_cost", input_rate)

        # Non-cached input tokens = total input - cache hits
        fresh_input = max(0, input_tokens - cache_read_tokens)
        cost = (
            (fresh_input * input_rate)
            + (cache_read_tokens * cache_rate)
            + (output_tokens * output_rate)
        )
        return round(cost, 6)
    except Exception:
        return None


def _resolve_proxy_model() -> str | None:
    """Read the actual model from the LiteLLM proxy config."""
    try:
        import yaml

        config_path = Path.home() / ".syke" / "litellm_config.yaml"
        if not config_path.exists():
            return None
        cfg = yaml.safe_load(config_path.read_text())
        for entry in cfg.get("model_list", []):
            model = entry.get("litellm_params", {}).get("model")
            if model:
                return model
    except Exception:
        pass
    return None


def _budget_scale_factor(proxy_model: str) -> float:
    """Compute how much to scale the SDK budget to compensate for pricing mismatch.

    The SDK prices tokens as Sonnet (~$3/M in, $15/M out). If the actual model
    is cheaper, the SDK exhausts the budget too early. Returns the ratio of
    Sonnet cost to actual model cost so the budget can be scaled up.
    """
    try:
        import litellm

        actual = litellm.model_cost.get(proxy_model)
        if not actual:
            return 1.0

        actual_in = actual.get("input_cost_per_token", 0)
        actual_out = actual.get("output_cost_per_token", 0)
        if not actual_in or not actual_out:
            return 1.0

        # Sonnet 4 pricing (what the SDK assumes)
        sonnet_in = 3.0 / 1_000_000  # $3/M
        sonnet_out = 15.0 / 1_000_000  # $15/M

        # Weighted average assuming ~80% input, ~20% output (typical synthesis)
        sonnet_blend = 0.8 * sonnet_in + 0.2 * sonnet_out
        actual_blend = 0.8 * actual_in + 0.2 * actual_out

        if actual_blend <= 0:
            return 1.0

        return sonnet_blend / actual_blend
    except Exception:
        return 1.0


def _load_skill_file(content_override: str | None = None) -> tuple[str, str]:
    """Load skill file content and compute SHA256 hash. Returns (content, hash).

    content_override: if set, use this instead of the file on disk. Used by the
    replay sandbox to inject patched prompts for ablation conditions without
    touching the real skill file or using module-level global state.
    """
    if content_override is not None:
        h = hashlib.sha256(content_override.encode("utf-8")).hexdigest()
        return content_override, h
    try:
        content = _SKILL_FILE.read_text(encoding="utf-8").strip()
        if not content:
            log.warning("Skill file at %s is empty, using fallback", _SKILL_FILE)
            return _FALLBACK_PROMPT, hashlib.sha256(_FALLBACK_PROMPT.encode("utf-8")).hexdigest()
        skill_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return content, skill_hash
    except FileNotFoundError:
        log.error("Skill file not found at %s, using fallback", _SKILL_FILE)
        return _FALLBACK_PROMPT, hashlib.sha256(_FALLBACK_PROMPT.encode("utf-8")).hexdigest()
