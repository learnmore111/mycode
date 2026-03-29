"""Edit file tool — string replacement based editing. Equivalent to src/tool/edit.ts."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from opencode.tool.base import ToolInfo, ToolResult, ToolContext
from opencode.project.instance import current_or_none

class EditTool(ToolInfo):
    id = "edit"
    description = "Edit a file by replacing an exact string with new content. The old_string must match exactly (including whitespace)."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to edit"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        file_path = args["file_path"]
        old_string = args["old_string"]
        new_string = args["new_string"]

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path

        if not os.path.exists(full):
            return ToolResult(title=f"Edit {file_path}", output=f"File not found: {file_path}", metadata={"success": False})

        try:
            content = Path(full).read_text(encoding="utf-8")
            count = content.count(old_string)
            if count == 0:
                return ToolResult(
                    title=f"Edit {file_path}",
                    output="old_string not found in file. Make sure it matches exactly including whitespace.",
                    metadata={"success": False},
                )
            if count > 1:
                return ToolResult(
                    title=f"Edit {file_path}",
                    output=f"old_string found {count} times. It must be unique. Add more surrounding context.",
                    metadata={"success": False},
                )

            new_content = content.replace(old_string, new_string, 1)
            Path(full).write_text(new_content, encoding="utf-8")

            return ToolResult(
                title=f"Edit {file_path}",
                output=f"Successfully edited {file_path}",
                metadata={"success": True},
            )
        except Exception as e:
            return ToolResult(title=f"Edit {file_path}", output=f"Error: {e}", metadata={"success": False})

tool = EditTool()
