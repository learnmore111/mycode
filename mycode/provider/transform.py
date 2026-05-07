"""Provider-specific parameter transformations.

Handles provider-specific options like max output tokens, reasoning settings,
cache control, and tool choice adjustments.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mycode.util import log as logmod

if TYPE_CHECKING:
    from mycode.provider.schema import Model

logger = logmod.create(service="provider.transform")

OUTPUT_TOKEN_MAX = 32_000


def temperature(model: Model) -> float | None:
    """Get model-specific temperature. Matches original transform.ts temperature()."""
    mid = model.id.lower()
    if "qwen" in mid:
        return 0.55
    if "claude" in mid:
        return None  # Anthropic handles temperature differently
    if "gemini" in mid:
        return 1.0
    if "glm-4.6" in mid or "glm-4.7" in mid:
        return 1.0
    if "minimax-m2" in mid:
        return 1.0
    if "kimi-k2" in mid:
        if any(s in mid for s in ["thinking", "k2.", "k2p", "k2-5"]):
            return 1.0
        return 0.6
    return None


def top_p(model: Model) -> float | None:
    """Get model-specific top_p. Matches original transform.ts topP()."""
    mid = model.id.lower()
    if "qwen" in mid:
        return 1.0
    if any(s in mid for s in ["minimax-m2", "gemini", "kimi-k2.5", "kimi-k2p5", "kimi-k2-5"]):
        return 0.95
    return None


def max_tokens(model: Model) -> int | None:
    """Determine max output tokens for a model."""
    limit = model.limit.output
    if limit <= 0:
        return None
    return min(limit, OUTPUT_TOKEN_MAX)


def supports_cache(model: Model) -> bool:
    """Check if the model supports prompt caching."""
    npm = model.api.npm
    return npm in (
        "@ai-sdk/anthropic", "@ai-sdk/openai", "@ai-sdk/google",
        "@ai-sdk/amazon-bedrock", "@ai-sdk/deepinfra",
    )


def reasoning_params(model: Model, variant: str | None = None) -> dict[str, Any]:
    """Get reasoning/thinking parameters for a model."""
    if not model.capabilities.reasoning:
        return {}

    npm = model.api.npm
    params: dict[str, Any] = {}

    if "anthropic" in npm:
        budget = 10000
        if variant == "think_hard":
            budget = 30000
        elif variant == "think_quick":
            budget = 3000
        params["thinking"] = {"type": "enabled", "budget_tokens": budget}

    elif "openai" in npm:
        if variant == "think_hard":
            params["reasoning_effort"] = "high"
        elif variant == "think_quick":
            params["reasoning_effort"] = "low"
        else:
            params["reasoning_effort"] = "medium"

    elif "google" in npm:
        if variant == "think_hard":
            params["thinking"] = {"thinkingBudget": 24576}

    return params


def provider_options(model: Model, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build provider-specific options for a model call."""
    opts: dict[str, Any] = {}

    # Max tokens
    mt = max_tokens(model)
    if mt:
        opts["max_tokens"] = mt

    # Merge model-level options
    if model.options:
        opts.update(model.options)

    # Merge extra
    if extra:
        opts.update(extra)

    return opts


def build_litellm_kwargs(model: Model, variant: str | None = None) -> dict[str, Any]:
    """Build kwargs to pass to litellm.acompletion for a specific model."""
    kwargs: dict[str, Any] = {}

    mt = max_tokens(model)
    if mt:
        kwargs["max_tokens"] = mt

    # Model-specific temperature
    temp = temperature(model)
    if temp is not None:
        kwargs["temperature"] = temp

    # Model-specific top_p
    tp = top_p(model)
    if tp is not None:
        kwargs["top_p"] = tp

    # Reasoning / thinking params (Anthropic/OpenAI/Google style)
    reasoning = reasoning_params(model, variant)
    if reasoning:
        kwargs.update(reasoning)

    # Model-level headers
    if model.headers:
        kwargs.setdefault("extra_headers", {}).update(model.headers)

    # DeepSeek-style thinking via extra_body (e.g., {"thinking": {"type": "enabled"}})
    # This is needed for providers that accept thinking as a non-standard body param.
    _apply_thinking_extra_body(model, kwargs)

    return kwargs


def _apply_thinking_extra_body(model: Model, kwargs: dict[str, Any]) -> None:
    """Inject model-level ``thinking`` config into extra_body if present.

    Used for DeepSeek-style thinking param that must be sent via
    ``extra_body={"thinking": {"type": "enabled"}}``.
    """
    thinking = model.thinking
    if not isinstance(thinking, dict):
        return
    kwargs.setdefault("extra_body", {})["thinking"] = thinking
