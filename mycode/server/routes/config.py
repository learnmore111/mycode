"""Config API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from mycode.config import config as configmod

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def config_get(directory: str = Query(default=".")) -> Any:
    cfg = configmod.get(directory)
    return cfg.model_dump(exclude_none=True)


@router.post("")
async def config_update(request: Request) -> Any:
    body = await request.json()
    updated = configmod.update_global(body)
    return updated.model_dump(exclude_none=True)
