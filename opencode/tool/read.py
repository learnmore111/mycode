"""Read file tool. Equivalent to src/tool/read.ts."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from opencode.project.instance import current_or_none
from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class ReadTool(ToolInfo):
    id = "read"
    description = "Read the contents of a file. Use line_offset and line_count for partial reads."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to read (relative to project root)"},
                "line_offset": {"type": "integer", "description": "Starting line number (0-based)"},
                "line_count": {"type": "integer", "description": "Number of lines to read"},
            },
            "required": ["file_path"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        file_path = args["file_path"]
        offset = args.get("line_offset", 0)
        count = args.get("line_count")

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path

        if not os.path.exists(full):
            return ToolResult(title=f"Read {file_path}", output=f"File not found: {file_path}", metadata={})

        try:
            content = Path(full).read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            total = len(lines)

            if offset or count:
                end = min(offset + count, total) if count else total
                lines = lines[offset:end]

            numbered = "\n".join(f"{i + offset + 1:6d}:{line}" for i, line in enumerate(lines))
            return ToolResult(
                title=f"Read {file_path}",
                output=numbered,
                metadata={"lines": len(lines), "total": total},
            )
        except Exception as e:
            return ToolResult(title=f"Read {file_path}", output=f"Error reading file: {e}", metadata={})

tool = ReadTool()
