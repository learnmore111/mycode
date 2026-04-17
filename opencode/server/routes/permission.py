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
    """Reply to a pending permission request.

    Body: { "reply": "allow" | "always" | "reject", "message"?: string }

    "allow" is mapped to "once" internally (permit this single request).
    "always" adds a persistent allow rule for this permission.
    "reject" denies the request (optionally with a feedback message).
    """
    body = await request.json()
    raw_reply = body.get("reply", "allow")
    message = body.get("message")

    if not _manager:
        return {"ok": False, "error": "Permission manager not initialized"}

    # Map frontend "allow" to backend "once"
    reply_map = {"allow": "once", "once": "once", "always": "always", "reject": "reject"}
    reply = reply_map.get(raw_reply, "once")

    await _manager.reply(request_id=request_id, reply=reply, message=message)
    return {"ok": True}
