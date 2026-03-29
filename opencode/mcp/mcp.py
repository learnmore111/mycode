"""MCP (Model Context Protocol) support. Equivalent to src/mcp/index.ts."""
from __future__ import annotations

import contextlib
import os
from typing import Any

from opencode.util import log as logmod

logger = logmod.create(service="mcp")


class McpServer:
    """A connected MCP server."""
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.config = config
        self.status: str = "disabled"  # connected | disabled | failed | needs_auth
        self.tools: list[dict[str, Any]] = []
        self._client: Any = None
        self._context_stack: list[Any] = []  # Keep context managers alive

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
            logger.info("connected", name=self.name, type=server_type)
        except Exception as e:
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

            # Enter context managers manually and keep them alive
            stdio_ctx = stdio_client(server_params)
            read_stream, write_stream = await stdio_ctx.__aenter__()
            self._context_stack.append(stdio_ctx)

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            self._context_stack.append(session_ctx)

            await session.initialize()
            tools_result = await session.list_tools()
            self.tools = [{"name": t.name, "description": t.description or "",
                           "inputSchema": t.inputSchema} for t in tools_result.tools]
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

            await session.initialize()
            tools_result = await session.list_tools()
            self.tools = [{"name": t.name, "description": t.description or "",
                           "inputSchema": t.inputSchema} for t in tools_result.tools]
            self._client = session
            logger.info("MCP remote connected", name=self.name, tools=len(self.tools))
        except ImportError:
            logger.warn("mcp SDK streamable_http not available, using stub", name=self.name)
        except Exception as e:
            raise ConnectionError(f"MCP remote connection failed: {e}") from e

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on this MCP server."""
        if not self._client:
            raise RuntimeError(f"MCP server {self.name} not connected")
        result = await self._client.call_tool(tool_name, arguments)
        return result

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
