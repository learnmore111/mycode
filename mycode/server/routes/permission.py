"""Permission API routes."""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

if TYPE_CHECKING:
    from mycode.permission.permission import PermissionManager

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


@router.get("/rules")
async def permission_rules() -> dict[str, Any]:
    """Return the currently approved ruleset.

    This exposes the runtime ``_approved`` rules appended by "always"
    replies so a UI can present them as revocable entries.
    """
    if not _manager:
        return {"approved": []}
    approved = [
        {
            "permission": r.permission,
            "pattern": r.pattern,
            "action": r.action,
        }
        for r in _manager._approved  # noqa: SLF001 — intentional read for UI
    ]
    return {"approved": approved}


@router.delete("/rules")
async def permission_rules_clear(request: Request) -> dict[str, Any]:
    """Revoke approved rules.

    Body:
        {}                                            — clears everything
        {"permission": "edit"}                        — clears by permission
        {"permission": "edit", "pattern": "*.py"}     — clears one rule

    Returns the number of rules removed.
    """
    if not _manager:
        raise HTTPException(503, "Permission manager not initialized")

    body: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        body = await request.json()
    perm = body.get("permission") if isinstance(body, dict) else None
    pat = body.get("pattern") if isinstance(body, dict) else None

    before = list(_manager._approved)  # noqa: SLF001
    if perm is None and pat is None:
        _manager._approved.clear()  # noqa: SLF001
    else:
        _manager._approved[:] = [  # noqa: SLF001
            r for r in before
            if not (
                (perm is None or r.permission == perm)
                and (pat is None or r.pattern == pat)
            )
        ]
    removed = len(before) - len(_manager._approved)  # noqa: SLF001
    return {"removed": removed}


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
