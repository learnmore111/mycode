"""Authentication — API key and OAuth token management.

Stores provider credentials in the global data directory.
Equivalent to src/auth/ in the original.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from opencode.util import log as logmod
from opencode.util.paths import GlobalPaths

if TYPE_CHECKING:
    from pathlib import Path

logger = logmod.create(service="auth")


class ApiKeyAuth(BaseModel):
    type: Literal["api"]
    key: str


class OAuthAuth(BaseModel):
    type: Literal["oauth"]
    access: str
    refresh: str | None = None
    expires: int | None = None


class WellKnownAuth(BaseModel):
    type: Literal["wellknown"]
    key: str
    token: str


AuthInfo = ApiKeyAuth | OAuthAuth | WellKnownAuth


def _auth_dir() -> Path:
    return GlobalPaths.data() / "auth"


def _auth_file(provider_id: str) -> Path:
    return _auth_dir() / f"{provider_id}.json"


async def get(provider_id: str) -> AuthInfo | None:
    """Get stored auth info for a provider."""
    p = _auth_file(provider_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        auth_type = data.get("type")
        if auth_type == "api":
            return ApiKeyAuth(**data)
        if auth_type == "oauth":
            return OAuthAuth(**data)
        if auth_type == "wellknown":
            return WellKnownAuth(**data)
        return None
    except Exception as e:
        logger.warn("failed to read auth", provider=provider_id, error=str(e))
        return None


async def set_(provider_id: str, info: AuthInfo) -> None:
    """Store auth info for a provider."""
    p = _auth_file(provider_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(info.model_dump_json(indent=2), encoding="utf-8")
    logger.info("auth set", provider=provider_id, type=info.type)


async def remove(provider_id: str) -> None:
    """Remove stored auth for a provider."""
    p = _auth_file(provider_id)
    if p.exists():
        p.unlink()
        logger.info("auth removed", provider=provider_id)


async def all_() -> dict[str, AuthInfo]:
    """Get all stored auth entries."""
    result: dict[str, AuthInfo] = {}
    d = _auth_dir()
    if not d.exists():
        return result
    for p in d.glob("*.json"):
        provider_id = p.stem
        info = await get(provider_id)
        if info:
            result[provider_id] = info
    return result
