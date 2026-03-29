"""Models.dev database loader — fetches provider/model metadata from models.dev.

Equivalent to src/provider/models.ts. Provides the model catalog that maps
provider IDs to their available models with capabilities, costs, and limits.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path
from typing import Any
import httpx
from opencode.util import log as logmod
from opencode.util.paths import GlobalPaths

logger = logmod.create(service="models_dev")

MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_TTL = 3600 * 6  # 6 hours


def _cache_path() -> Path:
    return GlobalPaths.cache() / "models_dev.json"


def _load_cache() -> dict[str, Any] | None:
    p = _cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("_ts", 0) < CACHE_TTL:
            return data.get("providers", {})
    except Exception:
        pass
    return None


def _save_cache(providers: dict[str, Any]) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps({"_ts": time.time(), "providers": providers}), encoding="utf-8")
    except Exception:
        pass


async def fetch() -> dict[str, Any]:
    """Fetch the models.dev database. Returns {provider_id: provider_data}."""
    # Check cache first
    cached = _load_cache()
    if cached is not None:
        logger.debug("using cached models.dev data", count=len(cached))
        return cached

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(MODELS_DEV_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warn("failed to fetch models.dev, using fallback", error=str(e))
        # Return empty — provider will only use config-defined models
        return {}

    # models.dev returns a flat list or dict of providers with nested models
    providers: dict[str, Any] = {}
    if isinstance(data, dict):
        providers = data
    elif isinstance(data, list):
        for p in data:
            if isinstance(p, dict) and "id" in p:
                providers[p["id"]] = p

    _save_cache(providers)
    logger.info("fetched models.dev", providers=len(providers))
    return providers


def get_sync() -> dict[str, Any]:
    """Synchronous version — load from cache only."""
    return _load_cache() or {}
