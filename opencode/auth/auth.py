"""Authentication — API key and OAuth token management.

Enhanced with:
- Token expiry detection (OAuth tokens)
- Environment variable auto-discovery for provider keys
- Authentication status helpers (is_authenticated, auth_source)
- Stores provider credentials in the global data directory.
"""

from __future__ import annotations

import json
import os
import time
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
    expires: int | None = None  # Unix timestamp (seconds)

    @property
    def is_expired(self) -> bool:
        """Check if the OAuth token has expired."""
        if self.expires is None:
            return False
        return time.time() > self.expires

    @property
    def expires_in_seconds(self) -> int | None:
        """Seconds until token expires. None if no expiry. Negative if expired."""
        if self.expires is None:
            return None
        return int(self.expires - time.time())


class WellKnownAuth(BaseModel):
    type: Literal["wellknown"]
    key: str
    token: str


AuthInfo = ApiKeyAuth | OAuthAuth | WellKnownAuth


# Well-known env vars for common providers (auto-discovery)
_ENV_MAP: dict[str, list[str]] = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "cohere": ["COHERE_API_KEY", "CO_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
}


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


# ---------------------------------------------------------------------------
# Auth status helpers
# ---------------------------------------------------------------------------


def get_env_key(provider_id: str) -> str | None:
    """Try to find an API key from environment variables for a provider."""
    env_keys = _ENV_MAP.get(provider_id, [])
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            return val
    return None


def auth_source(provider_id: str) -> Literal["stored", "env", "none"]:
    """Determine where authentication comes from for a provider.

    Returns:
        "stored" - API key/token in data dir
        "env"    - API key found in environment variables
        "none"   - No authentication available
    """
    p = _auth_file(provider_id)
    if p.exists():
        return "stored"
    if get_env_key(provider_id):
        return "env"
    return "none"


async def is_authenticated(provider_id: str) -> bool:
    """Check if a provider has valid (non-expired) authentication."""
    info = await get(provider_id)
    if info:
        return not (isinstance(info, OAuthAuth) and info.is_expired)
    return get_env_key(provider_id) is not None
