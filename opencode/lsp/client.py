"""LSP client — JSON-RPC communication with language servers. Equivalent to src/lsp/client.ts."""
from __future__ import annotations
import asyncio, json, os
from typing import Any
from opencode.util import log as logmod

logger = logmod.create(service="lsp.client")

_MSG_ID = 0


def _next_id() -> int:
    global _MSG_ID
    _MSG_ID += 1
    return _MSG_ID


class LspJsonRpcClient:
    """Minimal LSP JSON-RPC client over stdio."""

    def __init__(self, process: asyncio.subprocess.Process, server_id: str, root: str):
        self.process = process
        self.server_id = server_id
        self.root = root
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._initialized = False

    async def start(self) -> None:
        """Initialize the LSP server connection."""
        self._reader_task = asyncio.create_task(self._read_loop())
        # Send initialize
        result = await self.request("initialize", {
            "processId": os.getpid(),
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"relatedInformation": True},
                    "hover": {"contentFormat": ["plaintext"]},
                    "definition": {},
                    "references": {},
                    "documentSymbol": {},
                },
                "workspace": {"symbol": {"dynamicRegistration": False}},
            },
            "rootUri": f"file://{self.root}",
            "workspaceFolders": [{"uri": f"file://{self.root}", "name": os.path.basename(self.root)}],
        })
        await self.notify("initialized", {})
        self._initialized = True
        logger.info("LSP initialized", server=self.server_id, root=self.root)
        return result

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a JSON-RPC request and wait for response."""
        msg_id = _next_id()
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        self._send(msg)
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            logger.warn("LSP request timeout", method=method, server=self.server_id)
            return None

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self._send(msg)

    def _send(self, msg: dict[str, Any]) -> None:
        if not self.process.stdin:
            return
        body = json.dumps(msg)
        header = f"Content-Length: {len(body.encode())}\r\n\r\n"
        self.process.stdin.write((header + body).encode())

    async def _read_loop(self) -> None:
        """Read JSON-RPC messages from stdout."""
        if not self.process.stdout:
            return
        try:
            while True:
                # Read headers
                header_data = b""
                while b"\r\n\r\n" not in header_data:
                    chunk = await self.process.stdout.read(1)
                    if not chunk:
                        return
                    header_data += chunk

                headers = header_data.decode(errors="replace")
                content_length = 0
                for line in headers.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        content_length = int(line.split(":", 1)[1].strip())

                if content_length <= 0:
                    continue

                body = await self.process.stdout.readexactly(content_length)
                msg = json.loads(body.decode("utf-8"))
                self._handle_message(msg)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.error("LSP read error", server=self.server_id, error=str(e))

    def _handle_message(self, msg: dict[str, Any]) -> None:
        # Response
        if "id" in msg and "id" in msg:
            msg_id = msg["id"]
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                if "error" in msg:
                    future.set_result(msg["error"])
                else:
                    future.set_result(msg.get("result"))
            return

        # Notification
        method = msg.get("method", "")
        params = msg.get("params", {})
        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diags = params.get("diagnostics", [])
            self.diagnostics[uri] = diags

    async def open_file(self, path: str) -> None:
        """Notify the server about an opened file."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            return

        ext_map = {".py": "python", ".ts": "typescript", ".js": "javascript",
                   ".go": "go", ".rs": "rust", ".c": "c", ".cpp": "cpp"}
        ext = os.path.splitext(path)[1]
        lang = ext_map.get(ext, ext.lstrip(".") or "plaintext")

        await self.notify("textDocument/didOpen", {
            "textDocument": {"uri": f"file://{path}", "languageId": lang, "version": 1, "text": text},
        })

    async def hover(self, path: str, line: int, char: int) -> Any:
        return await self.request("textDocument/hover", {
            "textDocument": {"uri": f"file://{path}"},
            "position": {"line": line, "character": char},
        })

    async def definition(self, path: str, line: int, char: int) -> Any:
        return await self.request("textDocument/definition", {
            "textDocument": {"uri": f"file://{path}"},
            "position": {"line": line, "character": char},
        })

    async def references(self, path: str, line: int, char: int) -> Any:
        return await self.request("textDocument/references", {
            "textDocument": {"uri": f"file://{path}"},
            "position": {"line": line, "character": char},
            "context": {"includeDeclaration": True},
        })

    async def document_symbols(self, path: str) -> Any:
        return await self.request("textDocument/documentSymbol", {
            "textDocument": {"uri": f"file://{path}"},
        })

    async def workspace_symbols(self, query: str) -> Any:
        return await self.request("workspace/symbol", {"query": query})

    async def shutdown(self) -> None:
        """Shut down the LSP server."""
        if self._initialized:
            try:
                await asyncio.wait_for(self.request("shutdown", {}), timeout=5.0)
                await self.notify("exit", {})
            except Exception:
                pass
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
        if self._reader_task:
            self._reader_task.cancel()
