"""Predefined LSP server configurations. Equivalent to src/lsp/server.ts."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class LspServerDef:
    id: str
    extensions: list[str]
    command: list[str]

# Common LSP servers (subset of the 20+ in original)
SERVERS: dict[str, LspServerDef] = {
    "typescript": LspServerDef(
        id="typescript", extensions=[".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"],
        command=["typescript-language-server", "--stdio"],
    ),
    "pyright": LspServerDef(
        id="pyright", extensions=[".py", ".pyi"],
        command=["pyright-langserver", "--stdio"],
    ),
    "gopls": LspServerDef(
        id="gopls", extensions=[".go"],
        command=["gopls", "serve"],
    ),
    "rust-analyzer": LspServerDef(
        id="rust-analyzer", extensions=[".rs"],
        command=["rust-analyzer"],
    ),
    "clangd": LspServerDef(
        id="clangd", extensions=[".c", ".cpp", ".cc", ".h", ".hpp"],
        command=["clangd"],
    ),
    "lua-language-server": LspServerDef(
        id="lua-language-server", extensions=[".lua"],
        command=["lua-language-server"],
    ),
}
