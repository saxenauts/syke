"""Anthropic client wrapper — retries, token tracking, cost calculation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import anthropic

from syke.config import ANTHROPIC_API_KEY, DEFAULT_MODEL, THINKING_BUDGET


@dataclass
class LLMResponse:
    """Response from an LLM call with usage tracking."""

    content: str
    thinking: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    model: str = ""
    cost_usd: float = 0.0


# Pricing per million tokens (Opus 4.6)
PRICING = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0, "thinking": 75.0},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "thinking": 15.0},
}


def _calc_cost(model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float:
    prices = PRICING.get(model, PRICING["claude-opus-4-6"])
    return (
        input_tokens * prices["input"] / 1_000_000
        + output_tokens * prices["output"] / 1_000_000
        + thinking_tokens * prices["thinking"] / 1_000_000
    )


class LLMClient:
    """Wrapper around the Anthropic SDK with retries and cost tracking."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = anthropic.Anthropic(api_key=api_key or ANTHROPIC_API_KEY)
        self.model = model or DEFAULT_MODEL
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_thinking_tokens = 0
        self.total_cost = 0.0

    def _stream_request(self, kwargs: dict):
        """Use streaming to collect a full response (avoids SDK timeout for long requests)."""
        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 16000,
        temperature: float = 1.0,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> LLMResponse:
        """Send a chat completion request with optional extended thinking."""
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        if system:
            kwargs["system"] = system

        if thinking:
            budget = thinking_budget or THINKING_BUDGET
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
            # max_tokens must exceed thinking budget
            if kwargs["max_tokens"] <= budget:
                kwargs["max_tokens"] = budget + 16000
            kwargs["temperature"] = 1.0
        else:
            kwargs["temperature"] = temperature

        # Retry with backoff (stream for thinking requests to avoid 10min timeout)
        last_error = None
        for attempt in range(3):
            try:
                if thinking:
                    response = self._stream_request(kwargs)
                else:
                    response = self.client.messages.create(**kwargs)
                break
            except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
                last_error = e
                time.sleep(2 ** attempt)
        else:
            raise last_error  # type: ignore

        # Parse response
        content_text = ""
        thinking_text = ""
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "thinking":
                thinking_text += block.thinking

        # Extract token usage — handle both regular and streaming response shapes
        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        # The Anthropic API includes thinking tokens inside output_tokens.
        # There's no separate field. We estimate thinking tokens from the
        # ratio of thinking text to total output text (rough but useful for
        # metrics visibility). Both are billed at the same output rate.
        thinking_tokens = 0
        if thinking_text:
            total_text_len = len(content_text) + len(thinking_text)
            if total_text_len > 0:
                thinking_ratio = len(thinking_text) / total_text_len
                thinking_tokens = int(output_tokens * thinking_ratio)

        # Cost: output_tokens already includes thinking, so don't double-count.
        # Pass thinking_tokens=0 to _calc_cost since output_tokens covers everything.
        cost = _calc_cost(self.model, input_tokens, output_tokens, thinking_tokens=0)

        # Track totals
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_thinking_tokens += thinking_tokens
        self.total_cost += cost

        return LLMResponse(
            content=content_text,
            thinking=thinking_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thinking_tokens=thinking_tokens,
            model=self.model,
            cost_usd=cost,
        )
