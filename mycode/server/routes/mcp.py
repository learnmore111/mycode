"""MCP API routes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from mycode.mcp.mcp import McpManager

router = APIRouter(prefix="/mcp", tags=["mcp"])

# Shared MCP manager — initialized by app startup
_manager: McpManager | None = None


def set_manager(manager: McpManager) -> None:
    global _manager
    _manager = manager


@router.get("")
async def mcp_status() -> dict[str, Any]:
    """Get status of all MCP servers."""
    if not _manager:
        return {"servers": {}}
    return {"servers": _manager.status(), "tools": list(_manager.tools().keys())}


@router.post("/{name}/connect")
async def mcp_connect(name: str):
    """Connect to an MCP server."""
    if not _manager:
        raise HTTPException(503, "MCP manager not initialized")
    await _manager.connect(name)
    return {"ok": True, "status": _manager.status().get(name, "unknown")}


@router.post("/{name}/disconnect")
async def mcp_disconnect(name: str):
    """Disconnect from an MCP server."""
    if not _manager:
        raise HTTPException(503, "MCP manager not initialized")
    await _manager.disconnect(name)
    return {"ok": True}
