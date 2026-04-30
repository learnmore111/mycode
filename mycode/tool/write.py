"""Write file tool.

Features:
- Path safety validation (prevent writing outside project directory)
- Atomic write (temp file + rename to prevent corruption)
- Returns a preview snippet of the written content (first/last lines)
- Shows file size and line count summary
- Capability declarations (is_destructive=True, is_concurrency_safe=False)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mycode.project.instance import current_or_none
from mycode.tool.base import (
    CallableTool,
    ToolContext,
    ToolError,
    ToolOk,
    ToolResult,
    _assert_file_read,
    atomic_write,
    resolve_tool_path,
)

_PREVIEW_LINES = 10
_MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10 MB limit for write operations


class WriteParams(BaseModel):
    """Parameters for the write tool."""
    file_path: str = Field(description="Path to the file to write (relative to project root or absolute)")
    content: str = Field(description="Content to write to the file")


class WriteTool(CallableTool[WriteParams]):
    id = "write"
    description = "Write content to a file. Creates the file and parent directories if they don't exist. Overwrites existing content."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        return True  # Overwrites are irreversible

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return False  # File writes are not concurrency-safe

    async def call(self, params: WriteParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        content = params.content

        # File size limit to prevent DoS
        if len(content) > _MAX_CONTENT_SIZE:
            return ToolError(
                f"Content too large: {len(content)} bytes exceeds {_MAX_CONTENT_SIZE // (1024 * 1024)}MB limit.",
                title=f"Write {file_path}",
                metadata={"success": False},
            )

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        full, path_error = resolve_tool_path(file_path, base)
        if path_error:
            return ToolError(path_error, title=f"Write {file_path}", metadata={"success": False})

        existed = os.path.exists(full)

        # --- Read-before-edit guard (only for overwrites) ---
        if existed:
            read_err = _assert_file_read(ctx.session_id, full)
            if read_err:
                return ToolError(read_err, title=f"Write {file_path}", metadata={"success": False})

        try:
            old_lines = 0
            if existed:
                old_lines = Path(full).read_text(encoding="utf-8", errors="replace").count("\n") + 1

            atomic_write(full, content)
            new_lines = content.count("\n") + 1
            file_size = Path(full).stat().st_size

            if existed:
                msg = f"Overwrote {file_path} ({old_lines} → {new_lines} lines, {_human_size(file_size)})"
            else:
                msg = f"Created {file_path} ({new_lines} lines, {_human_size(file_size)})"

            lines = content.split("\n")
            if new_lines <= _PREVIEW_LINES * 2:
                preview = "\n".join(f"{i + 1:6d}:{line}" for i, line in enumerate(lines))
            else:
                head = "\n".join(f"{i + 1:6d}:{line}" for i, line in enumerate(lines[:_PREVIEW_LINES]))
                tail_start = new_lines - _PREVIEW_LINES
                tail = "\n".join(
                    f"{tail_start + i + 1:6d}:{line}"
                    for i, line in enumerate(lines[tail_start:])
                )
                preview = f"{head}\n   ...({new_lines - _PREVIEW_LINES * 2} lines omitted)...\n{tail}"

            result = ToolOk(
                f"{msg}\n\n{preview}",
                title=f"Write {file_path}",
                metadata={
                    "success": True,
                    "lines": new_lines,
                    "file_size": file_size,
                    "created": not existed,
                },
            )
            await _append_lsp_diagnostics(full, result)
            return result
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Write {file_path}", metadata={"success": False})


async def _append_lsp_diagnostics(file_path: str, result: ToolResult) -> None:
    """Touch file with LSP and append any diagnostics to the result output."""
    try:
        from mycode.lsp.lsp import get_lsp_manager
        lsp = get_lsp_manager()
        await lsp.touch_file(file_path)
        import asyncio
        await asyncio.sleep(0.3)
        diagnostics = await lsp.diagnostics()
        normalized = os.path.normpath(file_path)
        issues = diagnostics.get(normalized, [])
        errors = [d for d in issues if d.get("severity") == 1]
        if errors:
            MAX_DIAGNOSTICS_PER_FILE = 20
            limited = errors[:MAX_DIAGNOSTICS_PER_FILE]
            suffix = f"\n... and {len(errors) - len(limited)} more" if len(errors) > len(limited) else ""
            diag_lines = []
            for d in limited:
                line = d.get("range", {}).get("start", {}).get("line", 0) + 1
                col = d.get("range", {}).get("start", {}).get("character", 0) + 1
                msg = d.get("message", "")
                diag_lines.append(f"ERROR [{line}:{col}] {msg}")
            result.output += (
                f"\n\nLSP errors detected in this file, please fix:\n"
                f"<diagnostics file=\"{file_path}\">\n"
                f"{chr(10).join(diag_lines)}{suffix}\n"
                f"</diagnostics>"
            )
    except Exception:
        pass  # LSP diagnostics are best-effort


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    value: float = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


tool = WriteTool()
