"""Predefined LSP server configurations. Equivalent to src/lsp/server.ts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LspServerDef:
    id: str
    extensions: list[str]
    command: list[str]


# 20+ LSP servers matching the original TypeScript version
SERVERS: dict[str, LspServerDef] = {
    # --- Web / JavaScript / TypeScript ---
    "typescript": LspServerDef(
        id="typescript", extensions=[".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"],
        command=["typescript-language-server", "--stdio"],
    ),
    "eslint": LspServerDef(
        id="eslint", extensions=[".ts", ".tsx", ".js", ".jsx"],
        command=["vscode-eslint-language-server", "--stdio"],
    ),
    "css": LspServerDef(
        id="css", extensions=[".css", ".scss", ".less"],
        command=["vscode-css-language-server", "--stdio"],
    ),
    "html": LspServerDef(
        id="html", extensions=[".html", ".htm"],
        command=["vscode-html-language-server", "--stdio"],
    ),
    "json": LspServerDef(
        id="json", extensions=[".json", ".jsonc"],
        command=["vscode-json-language-server", "--stdio"],
    ),
    "svelte": LspServerDef(
        id="svelte", extensions=[".svelte"],
        command=["svelteserver", "--stdio"],
    ),
    "vue": LspServerDef(
        id="vue", extensions=[".vue"],
        command=["vue-language-server", "--stdio"],
    ),
    "astro": LspServerDef(
        id="astro", extensions=[".astro"],
        command=["astro-ls", "--stdio"],
    ),
    # --- Python ---
    "pyright": LspServerDef(
        id="pyright", extensions=[".py", ".pyi"],
        command=["pyright-langserver", "--stdio"],
    ),
    # --- Go ---
    "gopls": LspServerDef(
        id="gopls", extensions=[".go"],
        command=["gopls", "serve"],
    ),
    # --- Rust ---
    "rust-analyzer": LspServerDef(
        id="rust-analyzer", extensions=[".rs"],
        command=["rust-analyzer"],
    ),
    # --- C/C++ ---
    "clangd": LspServerDef(
        id="clangd", extensions=[".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".m", ".mm"],
        command=["clangd"],
    ),
    # --- Java ---
    "jdtls": LspServerDef(
        id="jdtls", extensions=[".java"],
        command=["jdtls"],
    ),
    # --- C# ---
    "omnisharp": LspServerDef(
        id="omnisharp", extensions=[".cs", ".csx"],
        command=["OmniSharp", "--languageserver"],
    ),
    # --- Ruby ---
    "solargraph": LspServerDef(
        id="solargraph", extensions=[".rb", ".rake", ".gemspec"],
        command=["solargraph", "stdio"],
    ),
    # --- PHP ---
    "intelephense": LspServerDef(
        id="intelephense", extensions=[".php"],
        command=["intelephense", "--stdio"],
    ),
    # --- Lua ---
    "lua-language-server": LspServerDef(
        id="lua-language-server", extensions=[".lua"],
        command=["lua-language-server"],
    ),
    # --- Bash / Shell ---
    "bash-language-server": LspServerDef(
        id="bash-language-server", extensions=[".sh", ".bash", ".zsh"],
        command=["bash-language-server", "start"],
    ),
    # --- YAML ---
    "yaml-language-server": LspServerDef(
        id="yaml-language-server", extensions=[".yaml", ".yml"],
        command=["yaml-language-server", "--stdio"],
    ),
    # --- TOML ---
    "taplo": LspServerDef(
        id="taplo", extensions=[".toml"],
        command=["taplo", "lsp", "stdio"],
    ),
    # --- Markdown ---
    "marksman": LspServerDef(
        id="marksman", extensions=[".md"],
        command=["marksman", "server"],
    ),
    # --- Kotlin ---
    "kotlin-language-server": LspServerDef(
        id="kotlin-language-server", extensions=[".kt", ".kts"],
        command=["kotlin-language-server"],
    ),
    # --- Swift ---
    "sourcekit-lsp": LspServerDef(
        id="sourcekit-lsp", extensions=[".swift"],
        command=["sourcekit-lsp"],
    ),
    # --- Zig ---
    "zls": LspServerDef(
        id="zls", extensions=[".zig"],
        command=["zls"],
    ),
    # --- Elixir ---
    "elixir-ls": LspServerDef(
        id="elixir-ls", extensions=[".ex", ".exs"],
        command=["elixir-ls"],
    ),
    # --- Terraform ---
    "terraform-ls": LspServerDef(
        id="terraform-ls", extensions=[".tf", ".tfvars"],
        command=["terraform-ls", "serve"],
    ),
}
