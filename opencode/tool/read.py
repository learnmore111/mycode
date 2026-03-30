"""Read file tool. Equivalent to src/tool/read.ts."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class ReadParams(BaseModel):
    """Parameters for the read tool."""
    file_path: str = Field(description="Path to the file to read (relative to project root)")
    line_offset: int | None = Field(default=None, description="Starting line number (0-based)")
    line_count: int | None = Field(default=None, description="Number of lines to read")


class ReadTool(CallableTool[ReadParams]):
    id = "read"
    description = "Read the contents of a file. Use line_offset and line_count for partial reads."

    async def call(self, params: ReadParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        offset = params.line_offset or 0
        count = params.line_count

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path

        if not os.path.exists(full):
            return ToolError(f"File not found: {file_path}", title=f"Read {file_path}")

        try:
            content = Path(full).read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            total = len(lines)

            if offset or count:
                end = min(offset + count, total) if count else total
                lines = lines[offset:end]

            numbered = "\n".join(f"{i + offset + 1:6d}:{line}" for i, line in enumerate(lines))
            return ToolOk(
                numbered,
                title=f"Read {file_path}",
                metadata={"lines": len(lines), "total": total},
            )
        except Exception as e:
            return ToolError(f"Error reading file: {e}", title=f"Read {file_path}")


tool = ReadTool()
