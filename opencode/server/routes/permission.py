"""Permission API routes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from opencode.permission.permission import PermissionManager

router = APIRouter(prefix="/permission", tags=["permission"])

# Shared permission manager — initialized by app startup
_manager: PermissionManager | None = None


def set_manager(manager: PermissionManager) -> None:
    global _manager
    _manager = manager


@router.get("")
async def permission_list() -> list[dict[str, Any]]:
    """List pending permission requests."""
    if not _manager:
        return []
    return [
        {
            "id": req.id,
            "session_id": req.session_id,
            "permission": req.permission,
            "patterns": req.patterns,
            "metadata": req.metadata,
        }
        for req in _manager.list_pending()
    ]


@router.post("/{request_id}")
async def permission_reply(request_id: str, request: Request):
    """Reply to a pending permission request."""
    body = await request.json()
    reply = body.get("reply", "allow")  # "allow" | "reject" | "always"
    message = body.get("message")

    if not _manager:
        return {"ok": False, "error": "Permission manager not initialized"}

    await _manager.reply(request_id=request_id, reply=reply, message=message)
    return {"ok": True}
