"""Write file tool. Equivalent to src/tool/write.ts."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from opencode.project.instance import current_or_none
from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class WriteTool(ToolInfo):
    id = "write"
    description = "Write content to a file. Creates the file and parent directories if they don't exist. Overwrites existing content."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to write"},
                "content": {"type": "string", "description": "Content to write to the file"},
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        file_path = args["file_path"]
        content = args["content"]
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
        try:
            Path(full).parent.mkdir(parents=True, exist_ok=True)
            Path(full).write_text(content, encoding="utf-8")
            lines = content.count("\n") + 1
            return ToolResult(title=f"Write {file_path}", output=f"Wrote {lines} lines to {file_path}", metadata={"success": True, "lines": lines})
        except Exception as e:
            return ToolResult(title=f"Write {file_path}", output=f"Error: {e}", metadata={"success": False})

tool = WriteTool()
