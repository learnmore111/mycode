"""MCP API routes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from mycode.mcp.mcp import McpManager

router = APIRouter(prefix="/mcp", tags=["mcp"])

# Shared MCP manager — initialized by app startup
_manager: McpManager | None = None


def set_manager(manager: McpManager) -> None:
    global _manager
    _manager = manager


class _AddMcpServer(BaseModel):
    name: str
    type: str = "local"  # "local" or "remote"
    command: list[str] | None = None
    url: str | None = None
    environment: dict[str, str] | None = None
    headers: dict[str, str] | None = None


@router.get("")
async def mcp_status() -> dict[str, Any]:
    """Get status of all MCP servers."""
    if not _manager:
        return {"servers": {}, "tools": []}
    return {"servers": _manager.status(), "tools": list(_manager.tools().keys())}


@router.post("")
async def mcp_add(body: _AddMcpServer) -> Any:
    """Add a new MCP server to config and connect."""
    from mycode.config import config as configmod

    name = body.name.strip()
    if not name:
        raise HTTPException(400, "服务器名称不能为空")

    # Build config entry
    entry: dict[str, Any] = {"type": body.type}
    if body.type == "local":
        if not body.command:
            raise HTTPException(400, "本地 MCP 服务器需要 command 参数")
        entry["command"] = body.command
        if body.environment:
            entry["environment"] = body.environment
    elif body.type == "remote":
        if not body.url:
            raise HTTPException(400, "远程 MCP 服务器需要 url 参数")
        entry["url"] = body.url
        if body.headers:
            entry["headers"] = body.headers
    else:
        raise HTTPException(400, f"不支持的 MCP 类型: {body.type}")

    # Persist to global config
    configmod.update_global({"mcp": {name: entry}})

    # Initialize and connect if manager available
    if _manager:
        from mycode.mcp.mcp import McpServer
        server = McpServer(name, entry)
        _manager._servers[name] = server
        await server.connect()

    return {"ok": True, "name": name, "status": _manager.status().get(name) if _manager else "unknown"}


@router.delete("/{name}")
async def mcp_remove(name: str) -> Any:
    """Remove an MCP server from config and disconnect."""
    from mycode.config import config as configmod

    # Disconnect if running
    if _manager:
        await _manager.disconnect(name)
        _manager._servers.pop(name, None)

    # Remove from config: set to None to remove key
    cfg = configmod.get()
    mcp_cfg = cfg.model_dump(exclude_none=True).get("mcp", {})
    if name in mcp_cfg:
        mcp_cfg.pop(name)
        configmod.update_global({"mcp": mcp_cfg})

    return {"ok": True}


@router.post("/{name}/connect")
async def mcp_connect(name: str) -> Any:
    """Connect to an MCP server."""
    if not _manager:
        raise HTTPException(503, "MCP manager not initialized")
    await _manager.connect(name)
    return {"ok": True, "status": _manager.status().get(name, "unknown")}


@router.post("/{name}/disconnect")
async def mcp_disconnect(name: str) -> Any:
    """Disconnect from an MCP server."""
    if not _manager:
        raise HTTPException(503, "MCP manager not initialized")
    await _manager.disconnect(name)
    return {"ok": True}
