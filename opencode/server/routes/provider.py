"""Provider API routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from opencode.provider import provider as providermod

router = APIRouter(prefix="/provider", tags=["provider"])


@router.get("")
async def provider_list():
    providers = await providermod.list_providers()
    return {pid: {"id": p.id, "name": p.name, "source": p.source,
                   "models": {mid: {"id": m.id, "name": m.name} for mid, m in p.models.items()}}
            for pid, p in providers.items()}


@router.get("/{provider_id}")
async def provider_get(provider_id: str):
    p = await providermod.get_provider(provider_id)
    if not p:
        raise HTTPException(404, f"Provider not found: {provider_id}")
    return {"id": p.id, "name": p.name, "source": p.source,
            "models": {mid: {"id": m.id, "name": m.name} for mid, m in p.models.items()}}
