"""Write file tool. Equivalent to src/tool/write.ts."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class WriteParams(BaseModel):
    """Parameters for the write tool."""
    file_path: str = Field(description="Path to the file to write")
    content: str = Field(description="Content to write to the file")


class WriteTool(CallableTool[WriteParams]):
    id = "write"
    description = "Write content to a file. Creates the file and parent directories if they don't exist. Overwrites existing content."

    async def call(self, params: WriteParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        content = params.content
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        try:
            existed = os.path.exists(full)
            old_lines = 0
            if existed:
                old_lines = Path(full).read_text(encoding="utf-8", errors="replace").count("\n") + 1

            Path(full).parent.mkdir(parents=True, exist_ok=True)
            Path(full).write_text(content, encoding="utf-8")
            new_lines = content.count("\n") + 1

            if existed:
                msg = f"Overwrote {file_path} ({old_lines} → {new_lines} lines)"
            else:
                msg = f"Created {file_path} ({new_lines} lines)"

            return ToolOk(
                msg,
                title=f"Write {file_path}",
                metadata={"success": True, "lines": new_lines, "created": not existed},
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Write {file_path}", metadata={"success": False})


tool = WriteTool()
