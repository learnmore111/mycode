"""Write file tool. Equivalent to src/tool/write.ts.

Enhancements:
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

from opencode.project.instance import current_or_none
from opencode.tool.base import (
    CallableTool,
    ToolContext,
    ToolError,
    ToolOk,
    ToolResult,
    atomic_write,
    resolve_tool_path,
)

_PREVIEW_LINES = 10


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
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        full, path_error = resolve_tool_path(file_path, base)
        if path_error:
            return ToolError(path_error, title=f"Write {file_path}", metadata={"success": False})

        try:
            existed = os.path.exists(full)
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

            return ToolOk(
                f"{msg}\n\n{preview}",
                title=f"Write {file_path}",
                metadata={
                    "success": True,
                    "lines": new_lines,
                    "file_size": file_size,
                    "created": not existed,
                },
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Write {file_path}", metadata={"success": False})


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


tool = WriteTool()
