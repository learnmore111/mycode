from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

from mycode.util import log as logmod

logger = logmod.create(service="mcp")

_MAX_RECONNECT_ATTEMPTS = 3
_RECONNECT_DELAY = 2.0  # seconds


class McpServer:
    """A connected MCP server."""

    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.status: str = "disabled"  # connected | disabled | failed | needs_auth
        self.tools: list[dict[str, Any]] = []
        self._client: Any = None
        self._context_stack: list[Any] = []
        self._reconnect_attempts = 0

    async def connect(self) -> None:
        """Connect to the MCP server."""
        server_type = self.config.get("type", "local")
        enabled = self.config.get("enabled", True)
        if not enabled:
            self.status = "disabled"
            return

        try:
            if server_type == "local":
                await self._connect_local()
            elif server_type == "remote":
                await self._connect_remote()
            self.status = "connected"
            self._reconnect_attempts = 0
            logger.info("connected", name=self.name, type=server_type)
        except Exception as e:
            # Clean up partially initialized context stack on failure
            await self.disconnect()
            self.status = "failed"
            logger.error("connection failed", name=self.name, error=str(e))

    async def _connect_local(self) -> None:
        command = self.config.get("command", [])
        if not command:
            raise ValueError("MCP local server requires 'command'")
        env = {**os.environ, **(self.config.get("environment") or {})}
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(command=command[0], args=command[1:], env=env)
            stdio_ctx = stdio_client(server_params)
            read_stream, write_stream = await stdio_ctx.__aenter__()
            self._context_stack.append(stdio_ctx)

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            self._context_stack.append(session_ctx)

            await asyncio.wait_for(session.initialize(), timeout=30)
            await self._refresh_tools(session)
            self._client = session
            logger.info("MCP local connected", name=self.name, tools=len(self.tools))
        except ImportError:
            logger.warn("mcp SDK not available, using stub", name=self.name)
        except Exception as e:
            raise ConnectionError(f"MCP local connection failed: {e}") from e

    async def _connect_remote(self) -> None:
        url = self.config.get("url", "")
        if not url:
            raise ValueError("MCP remote server requires 'url'")
        headers = self.config.get("headers") or {}
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            http_ctx = streamablehttp_client(url, headers=headers)
            read_stream, write_stream, _ = await http_ctx.__aenter__()
            self._context_stack.append(http_ctx)

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            self._context_stack.append(session_ctx)

            await asyncio.wait_for(session.initialize(), timeout=30)
            await self._refresh_tools(session)
            self._client = session
            logger.info("MCP remote connected", name=self.name, tools=len(self.tools))
        except ImportError:
            logger.warn("mcp SDK streamable_http not available, using stub", name=self.name)
        except Exception as e:
            raise ConnectionError(f"MCP remote connection failed: {e}") from e

    async def _refresh_tools(self, session: Any = None) -> None:
        """Refresh the tool list from the server."""
        client = session or self._client
        if not client:
            return
        tools_result = await client.list_tools()
        self.tools = [
            {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
            for t in tools_result.tools
        ]

    async def refresh_tools(self) -> None:
        """Public method to refresh tools from a connected server."""
        if self.status != "connected" or not self._client:
            return
        try:
            await self._refresh_tools()
            logger.info("tools refreshed", name=self.name, count=len(self.tools))
        except Exception as e:
            logger.warn("tool refresh failed", name=self.name, error=str(e))

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on this MCP server. Auto-reconnects on failure."""
        if not self._client:
            raise RuntimeError(f"MCP server {self.name} not connected")
        try:
            return await self._client.call_tool(tool_name, arguments)
        except Exception as e:
            logger.warn("tool call failed, attempting reconnect", name=self.name, error=str(e))
            if await self._try_reconnect():
                return await self._client.call_tool(tool_name, arguments)
            raise

    async def _try_reconnect(self) -> bool:
        """Attempt to reconnect to the server with exponential backoff."""
        if self._reconnect_attempts >= _MAX_RECONNECT_ATTEMPTS:
            logger.error("max reconnect attempts reached", name=self.name)
            return False
        self._reconnect_attempts += 1
        delay = _RECONNECT_DELAY * (2 ** (self._reconnect_attempts - 1))  # Exponential backoff
        logger.info("reconnecting", name=self.name, attempt=self._reconnect_attempts, delay=delay)
        await self.disconnect()
        await asyncio.sleep(delay)
        await self.connect()
        return self.status == "connected"

    async def disconnect(self) -> None:
        """Disconnect — tear down context managers in reverse order."""
        self._client = None
        for ctx in reversed(self._context_stack):
            with contextlib.suppress(Exception):
                await ctx.__aexit__(None, None, None)
        self._context_stack.clear()
        self.tools.clear()
        self.status = "disabled"


class McpManager:
    """Manages all MCP server connections."""

    def __init__(self) -> None:
        self._servers: dict[str, McpServer] = {}

    async def init(self, mcp_config: dict[str, Any] | None) -> None:
        if not mcp_config:
            return
        for name, config in mcp_config.items():
            if not isinstance(config, dict) or "type" not in config:
                continue
            server = McpServer(name, config)
            self._servers[name] = server
            await server.connect()

    def status(self) -> dict[str, str]:
        return {name: s.status for name, s in self._servers.items()}

    def tools(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, server in self._servers.items():
            if server.status != "connected":
                continue
            for tool in server.tools:
                key = f"{name}_{tool.get('name', '')}"
                result[key] = {**tool, "_server": name}
        return result

    async def refresh_tools(self, name: str | None = None) -> None:
        """Refresh tool lists. If name is given, refresh just that server."""
        if name:
            server = self._servers.get(name)
            if server:
                await server.refresh_tools()
        else:
            for server in self._servers.values():
                await server.refresh_tools()

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on a specific MCP server."""
        server = self._servers.get(server_name)
        if not server:
            raise RuntimeError(f"MCP server {server_name} not found")
        return await server.call_tool(tool_name, arguments)

    async def connect(self, name: str) -> None:
        server = self._servers.get(name)
        if server:
            await server.connect()

    async def disconnect(self, name: str) -> None:
        server = self._servers.get(name)
        if server:
            await server.disconnect()

    async def close(self) -> None:
        for server in self._servers.values():
            await server.disconnect()
