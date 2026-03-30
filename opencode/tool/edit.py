"""Edit file tool — string replacement based editing. Equivalent to src/tool/edit.ts."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class EditParams(BaseModel):
    """Parameters for the edit tool."""
    file_path: str = Field(description="Path to the file to edit")
    old_string: str = Field(description="Exact string to find and replace")
    new_string: str = Field(description="Replacement string")


class EditTool(CallableTool[EditParams]):
    id = "edit"
    description = "Edit a file by replacing an exact string with new content. The old_string must match exactly (including whitespace)."

    async def call(self, params: EditParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        old_string = params.old_string
        new_string = params.new_string

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path

        if not os.path.exists(full):
            return ToolError(
                f"File not found: {file_path}",
                title=f"Edit {file_path}",
                metadata={"success": False},
            )

        try:
            content = Path(full).read_text(encoding="utf-8")
            count = content.count(old_string)
            if count == 0:
                return ToolError(
                    "old_string not found in file. Make sure it matches exactly including whitespace.",
                    title=f"Edit {file_path}",
                    metadata={"success": False},
                )
            if count > 1:
                return ToolError(
                    f"old_string found {count} times. It must be unique. Add more surrounding context.",
                    title=f"Edit {file_path}",
                    metadata={"success": False},
                )

            new_content = content.replace(old_string, new_string, 1)
            Path(full).write_text(new_content, encoding="utf-8")

            return ToolOk(
                f"Successfully edited {file_path}",
                title=f"Edit {file_path}",
                metadata={"success": True},
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Edit {file_path}", metadata={"success": False})


tool = EditTool()
