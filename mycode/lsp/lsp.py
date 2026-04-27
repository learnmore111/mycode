from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Any

from mycode.lsp.servers import SERVERS, LspServerDef
from mycode.project.instance import current_or_none
from mycode.util import log as logmod

if TYPE_CHECKING:
    from mycode.lsp.client import LspJsonRpcClient

logger = logmod.create(service="lsp")


class LspManager:
    """Manages LSP server connections for the project."""
    def __init__(self) -> None:
        self._clients: list[LspJsonRpcClient] = []
        self._servers: dict[str, LspServerDef] = dict(SERVERS)
        self._broken: set[str] = set()
        self._hook_registered = False

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
        # Wire the post-write hook exactly once per process — on every
        # successful atomic_write we'll fan a didChange out to LSPs that
        # already track the file.
        if not self._hook_registered:
            from mycode.tool.base import register_post_write_hook

            async def _on_post_write(path: str, content: str) -> None:
                await self.notify_changed(path, text=content)

            register_post_write_hook(_on_post_write)
            self._hook_registered = True

    async def touch_file(self, file_path: str) -> None:
        """Notify LSP servers about a file (spawn server if needed)."""
        import os

        from mycode.lsp.client import LspJsonRpcClient
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

    async def notify_changed(self, file_path: str, *, text: str | None = None) -> None:
        """Send textDocument/didChange to every LSP that has this file open.

        Call this after edit/write/apply_patch operations so diagnostics
        stay in sync with the on-disk content. We only notify servers
        that have already opened the file (via ``touch_file``); this
        avoids spawning a new LSP just to announce a change.

        Errors are swallowed — LSP diagnostics are best-effort.
        """
        import os

        ext = os.path.splitext(file_path)[1]
        for sid, server in self._servers.items():
            if server.extensions and ext not in server.extensions:
                continue
            for client in self._clients:
                if client.server_id != sid:
                    continue
                try:
                    await client.did_change(file_path, text=text)
                except Exception as exc:  # noqa: BLE001 — best effort
                    logger.debug("didChange failed, ignoring", server=sid, error=str(exc))

    def status(self) -> list[dict[str, str]]:
        return [{"id": c.server_id, "root": c.root, "status": getattr(c, "status", "unknown")} for c in self._clients]

    async def diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        """Collect diagnostics from all running LSP servers."""
        result: dict[str, list[dict[str, Any]]] = {}
        for client in self._clients:
            for uri, diags in client.diagnostics.items():
                if diags:
                    result[uri] = diags
        return result

    async def close(self) -> None:
        for client in self._clients:
            await client.shutdown()
        self._clients.clear()


# Global singleton — lazily initialized on first use so import-time
# side effects are avoided.
_lsp_manager: LspManager | None = None


def get_lsp_manager() -> LspManager:
    """Return the global LspManager singleton."""
    global _lsp_manager
    if _lsp_manager is None:
        _lsp_manager = LspManager()
    return _lsp_manager
