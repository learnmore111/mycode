"""LSP integration manager. Equivalent to src/lsp/index.ts."""
from __future__ import annotations
import asyncio, shutil
from typing import Any
from opencode.lsp.servers import SERVERS, LspServerDef
from opencode.project.instance import current_or_none
from opencode.util import log as logmod

logger = logmod.create(service="lsp")


class LspClient:
    """Represents a running LSP server connection."""
    def __init__(self, server_id: str, root: str):
        self.server_id = server_id
        self.root = root
        self.status = "connected"
        self._process: asyncio.subprocess.Process | None = None

    async def shutdown(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
        self.status = "stopped"


class LspManager:
    """Manages LSP server connections for the project."""
    def __init__(self) -> None:
        from opencode.lsp.client import LspJsonRpcClient
        self._clients: list[LspJsonRpcClient] = []
        self._servers: dict[str, LspServerDef] = dict(SERVERS)
        self._broken: set[str] = set()

    async def init(self, lsp_config: dict[str, Any] | bool | None = None) -> None:
        if lsp_config is False:
            self._servers.clear()
            return
        if isinstance(lsp_config, dict):
            for name, cfg in lsp_config.items():
                if isinstance(cfg, dict) and cfg.get("disabled"):
                    self._servers.pop(name, None)
                elif isinstance(cfg, dict) and cfg.get("command"):
                    self._servers[name] = LspServerDef(
                        id=name, extensions=cfg.get("extensions", []), command=cfg["command"])

    async def touch_file(self, file_path: str) -> None:
        """Notify LSP servers about a file (spawn server if needed)."""
        import os
        from opencode.lsp.client import LspJsonRpcClient
        ext = os.path.splitext(file_path)[1]
        inst = current_or_none()
        root = inst.directory if inst else os.getcwd()

        for sid, server in self._servers.items():
            if server.extensions and ext not in server.extensions:
                continue
            if sid in self._broken:
                continue
            if not shutil.which(server.command[0]):
                self._broken.add(sid)
                continue

            # Check if we already have a client for this server+root
            existing = next((c for c in self._clients if c.server_id == sid and c.root == root), None)
            if existing:
                await existing.open_file(file_path)
                continue

            # Spawn new LSP server
            try:
                proc = await asyncio.create_subprocess_exec(
                    *server.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=root,
                )
                client = LspJsonRpcClient(proc, sid, root)
                await asyncio.wait_for(client.start(), timeout=15.0)
                self._clients.append(client)
                await client.open_file(file_path)
                logger.info("LSP server spawned", server=sid, root=root)
            except Exception as e:
                self._broken.add(sid)
                logger.warn("LSP spawn failed", server=sid, error=str(e))

    def status(self) -> list[dict[str, str]]:
        return [{"id": c.server_id, "root": c.root, "status": c.status} for c in self._clients]

    async def diagnostics(self) -> dict[str, list[dict]]:
        # TODO: Collect diagnostics from running LSP servers
        return {}

    async def close(self) -> None:
        for client in self._clients:
            await client.shutdown()
        self._clients.clear()