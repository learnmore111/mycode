"""MCP (Model Context Protocol) support. Equivalent to src/mcp/index.ts."""
from __future__ import annotations
import asyncio, os
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
            # Use the mcp SDK's stdio_client context manager
            # For long-lived connections, we'd store the context; for now test connectivity
            async with stdio_client(server_params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
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
            async with streamablehttp_client(url, headers=headers) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
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

    async def disconnect(self) -> None:
        self.status = "disabled"
        self._client = None


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
                result[key] = tool
        return result

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
