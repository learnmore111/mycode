"""AI Provider management.

Manages AI providers, model discovery, and LLM instantiation.
Uses litellm as the unified LLM SDK (replacing Vercel AI SDK).
Equivalent to src/provider/provider.ts.
"""

from __future__ import annotations

import os
from typing import Any

from opencode.auth import auth as authmod
from opencode.config import config as configmod
from opencode.provider.schema import (
    CacheCost,
    Model,
    ModelApi,
    ModelCapabilities,
    ModelCost,
    ModelInputCapabilities,
    ModelLimit,
    ModelOutputCapabilities,
    ProviderID,
    ProviderInfo,
)
from opencode.util import log as logmod
from opencode.util.error import NamedError

logger = logmod.create(service="provider")

ModelNotFoundError = NamedError.create("ProviderModelNotFoundError")
InitError = NamedError.create("ProviderInitError")

# Well-known provider → litellm prefix mapping
LITELLM_PREFIX: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "gemini",
    "xai": "xai",
    "groq": "groq",
    "mistral": "mistral",
    "deepinfra": "deepinfra",
    "cohere": "cohere",
    "perplexity": "perplexity",
    "together_ai": "together_ai",
    "amazon-bedrock": "bedrock",
    "azure": "azure",
    "openrouter": "openrouter",
    "cerebras": "cerebras",
}

# Well-known env vars per provider
PROVIDER_ENV: dict[str, list[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "deepinfra": ["DEEPINFRA_API_KEY"],
    "cohere": ["COHERE_API_KEY", "CO_API_KEY"],
    "perplexity": ["PERPLEXITY_API_KEY"],
    "together_ai": ["TOGETHERAI_API_KEY"],
    "amazon-bedrock": ["AWS_ACCESS_KEY_ID"],
    "azure": ["AZURE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "cerebras": ["CEREBRAS_API_KEY"],
}

_state: dict[str, ProviderInfo] | None = None


async def _init_state() -> dict[str, ProviderInfo]:
    """Discover and initialize all available providers."""
    global _state
    if _state is not None:
        return _state

    providers: dict[str, ProviderInfo] = {}
    cfg = configmod.get()

    # Step 0: Load models.dev database and seed providers with models
    from opencode.provider import models_dev
    models_db = await models_dev.fetch()
    for pid, pdata in models_db.items():
        if not isinstance(pdata, dict):
            continue
        models_dict: dict[str, Model] = {}
        raw_models = pdata.get("models", {})
        if isinstance(raw_models, dict):
            for mid, mdata in raw_models.items():
                if not isinstance(mdata, dict):
                    continue
                status = mdata.get("status", "active")
                if status == "deprecated":
                    continue
                limit = mdata.get("limit", {})
                cost = mdata.get("cost", {})
                modalities = mdata.get("modalities", {})
                inp_mod = modalities.get("input", []) if isinstance(modalities, dict) else []
                out_mod = modalities.get("output", []) if isinstance(modalities, dict) else []
                provider_info = mdata.get("provider", {}) or {}
                models_dict[mid] = Model(
                    id=mid,
                    providerID=pid,
                    api=ModelApi(
                        id=mdata.get("id", mid),
                        url=provider_info.get("api", pdata.get("api", "")),
                        npm=provider_info.get("npm", pdata.get("npm", "@ai-sdk/openai-compatible")),
                    ),
                    name=mdata.get("name", mid),
                    family=mdata.get("family", ""),
                    capabilities=ModelCapabilities(
                        temperature=mdata.get("temperature", False),
                        reasoning=mdata.get("reasoning", False),
                        attachment=mdata.get("attachment", False),
                        toolcall=mdata.get("tool_call", True),
                        input=ModelInputCapabilities(
                            text="text" in inp_mod, audio="audio" in inp_mod,
                            image="image" in inp_mod, video="video" in inp_mod, pdf="pdf" in inp_mod,
                        ),
                        output=ModelOutputCapabilities(
                            text="text" in out_mod, audio="audio" in out_mod,
                            image="image" in out_mod, video="video" in out_mod, pdf="pdf" in out_mod,
                        ),
                    ),
                    cost=ModelCost(
                        input=cost.get("input", 0) if isinstance(cost, dict) else 0,
                        output=cost.get("output", 0) if isinstance(cost, dict) else 0,
                        cache=CacheCost(
                            read=cost.get("cache_read", 0) if isinstance(cost, dict) else 0,
                            write=cost.get("cache_write", 0) if isinstance(cost, dict) else 0,
                        ),
                    ),
                    limit=ModelLimit(
                        context=limit.get("context", 0) if isinstance(limit, dict) else 0,
                        output=limit.get("output", 0) if isinstance(limit, dict) else 0,
                    ),
                    status=status if status in ("active", "alpha", "beta") else "active",
                    options=mdata.get("options", {}),
                    headers=mdata.get("headers", {}),
                )
        if models_dict:
            providers[pid] = ProviderInfo(
                id=pid,
                name=pdata.get("name", pid),
                source="custom",
                env=pdata.get("env", []),
                models=models_dict,
            )

    # Step 1: Load from env vars — activates providers with API keys
    for provider_id, env_keys in PROVIDER_ENV.items():
        for key in env_keys:
            val = os.environ.get(key)
            if val:
                if provider_id in providers:
                    providers[provider_id].source = "env"
                    providers[provider_id].key = val if len(env_keys) == 1 else None
                else:
                    providers[provider_id] = ProviderInfo(
                        id=provider_id,
                        name=provider_id.replace("-", " ").title(),
                        source="env",
                        env=env_keys,
                        key=val if len(env_keys) == 1 else None,
                    )
                break

    # Step 2: Load from stored auth
    for provider_id, info in (await authmod.all_()).items():
        if hasattr(info, "key"):
            if provider_id in providers:
                providers[provider_id].source = "api"
                providers[provider_id].key = info.key
            else:
                providers[provider_id] = ProviderInfo(
                    id=provider_id,
                    name=provider_id.replace("-", " ").title(),
                    source="api",
                    key=info.key,
                )

    # Step 3: Load from config
    if cfg.provider:
        for provider_id, pcfg in cfg.provider.items():
            if provider_id in providers:
                if pcfg.options:
                    providers[provider_id].options.update(pcfg.options)
                if pcfg.name:
                    providers[provider_id].name = pcfg.name
            else:
                key = pcfg.options.get("apiKey") if pcfg.options else None
                providers[provider_id] = ProviderInfo(
                    id=provider_id,
                    name=pcfg.name or provider_id,
                    source="config",
                    env=pcfg.env or [],
                    options=pcfg.options or {},
                    key=key,
                )

            # Add configured models
            if pcfg.models:
                for model_id, mcfg in pcfg.models.items():
                    api_id = mcfg.id or model_id
                    providers[provider_id].models[model_id] = Model(
                        id=model_id,
                        providerID=provider_id,
                        api=ModelApi(
                            id=api_id,
                            url=pcfg.api or "",
                            npm=pcfg.npm or "@ai-sdk/openai-compatible",
                        ),
                        name=mcfg.name or model_id,
                        family=mcfg.family or "",
                        capabilities=ModelCapabilities(
                            temperature=mcfg.temperature or False,
                            reasoning=mcfg.reasoning or False,
                            attachment=mcfg.attachment or False,
                            toolcall=mcfg.tool_call if mcfg.tool_call is not None else True,
                        ),
                        cost=ModelCost(
                            input=mcfg.cost.input if mcfg.cost else 0,
                            output=mcfg.cost.output if mcfg.cost else 0,
                            cache=CacheCost(
                                read=mcfg.cost.cache_read if mcfg.cost else 0,
                                write=mcfg.cost.cache_write if mcfg.cost else 0,
                            ),
                        ),
                        limit=ModelLimit(
                            context=mcfg.limit.context if mcfg.limit else 0,
                            output=mcfg.limit.output if mcfg.limit else 0,
                        ),
                        status=mcfg.status or "active",
                        options=mcfg.options or {},
                        headers=mcfg.headers or {},
                    )

    # Filter disabled/enabled
    disabled = set(cfg.disabled_providers or [])
    enabled = set(cfg.enabled_providers) if cfg.enabled_providers else None

    # Only keep providers that have an API key or were explicitly configured
    activated_sources = {"env", "api", "config"}
    for pid in list(providers.keys()):
        if pid in disabled or (enabled and pid not in enabled):
            del providers[pid]
            continue
        p = providers[pid]
        # Remove providers from models.dev that have no API key
        if p.source == "custom" and not p.key and not p.models:
            del providers[pid]
            continue
        # Remove models from providers that were only loaded from models.dev but have no key
        if p.source == "custom" and not p.key:
            del providers[pid]
            continue
        # Remove empty providers
        if not p.models:
            del providers[pid]

    logger.info("providers initialized", count=len(providers), ids=list(providers.keys()))
    _state = providers
    return providers


async def list_providers() -> dict[str, ProviderInfo]:
    """List all available providers."""
    return await _init_state()


async def get_provider(provider_id: ProviderID) -> ProviderInfo | None:
    """Get a specific provider."""
    providers = await _init_state()
    return providers.get(provider_id)


async def get_model(provider_id: ProviderID, model_id: str) -> Model:
    """Get a specific model from a provider."""
    providers = await _init_state()
    provider = providers.get(provider_id)
    if not provider:
        raise ModelNotFoundError({"providerID": provider_id, "modelID": model_id})
    model = provider.models.get(model_id)
    if not model:
        raise ModelNotFoundError({"providerID": provider_id, "modelID": model_id})
    return model


def litellm_model_name(model: Model) -> str:
    """Convert a Model to the litellm model string format.

    Examples:
        anthropic/claude-sonnet-4-20250514
        openai/gpt-4o
        groq/llama-3.1-70b
    """
    prefix = LITELLM_PREFIX.get(model.provider_id, model.provider_id)
    return f"{prefix}/{model.api.id}"


async def default_model() -> tuple[ProviderID, str]:
    """Get the default model (provider_id, model_id)."""
    cfg = configmod.get()

    if cfg.model:
        return parse_model(cfg.model)

    providers = await _init_state()

    # Pick first available provider with models
    for provider in providers.values():
        if provider.models:
            model_id = next(iter(provider.models))
            return provider.id, model_id

    raise RuntimeError("No providers with models found. Set ANTHROPIC_API_KEY or similar env var.")


def parse_model(model: str) -> tuple[ProviderID, str]:
    """Parse 'provider/model' string into (provider_id, model_id)."""
    parts = model.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid model format: {model}. Expected 'provider/model'.")
    return parts[0], parts[1]


def invalidate() -> None:
    """Clear cached provider state."""
    global _state
    _state = None
