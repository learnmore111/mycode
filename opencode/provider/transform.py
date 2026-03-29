"""Provider-specific parameter transformations. Equivalent to src/provider/transform.ts.

Handles provider-specific options like max output tokens, reasoning settings,
cache control, and tool choice adjustments.
"""
from __future__ import annotations
from typing import Any
from opencode.provider.schema import Model
from opencode.util import log as logmod

logger = logmod.create(service="provider.transform")

OUTPUT_TOKEN_MAX = 64_000


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
    npm = model.api.npm
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

    # Reasoning
    reasoning = reasoning_params(model, variant)
    if reasoning:
        # litellm passes these through to the provider
        kwargs.update(reasoning)

    # Model-level headers
    if model.headers:
        kwargs.setdefault("extra_headers", {}).update(model.headers)

    return kwargs
