"""Edit file tool — string replacement based editing. Equivalent to src/tool/edit.ts.

Inspired by Anthropic's StrReplaceEditorTool best practices:
- Returns surrounding context after edit so the agent can verify the change
- Supports insert_after_line for line-based insertion
- Empty old_string appends to end of file
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult

# Number of context lines to show around the edit
_CONTEXT_LINES = 4


def _snippet_around(lines: list[str], start: int, end: int, context: int = _CONTEXT_LINES) -> str:
    """Return a numbered snippet around the [start, end) line range."""
    lo = max(0, start - context)
    hi = min(len(lines), end + context)
    parts: list[str] = []
    for i in range(lo, hi):
        marker = " " if i < start or i >= end else "|"
        parts.append(f"{i + 1:6d}{marker}{lines[i]}")
    return "\n".join(parts)


class EditParams(BaseModel):
    """Parameters for the edit tool."""
    file_path: str = Field(description="Path to the file to edit")
    old_string: str = Field(default="", description="Exact string to find and replace. Empty string means append to end of file.")
    new_string: str = Field(default="", description="Replacement string. Empty string with non-empty old_string means deletion.")
    insert_after_line: int | None = Field(default=None, description="Insert new_string after this line number (1-based). Ignores old_string when set.")


class EditTool(CallableTool[EditParams]):
    id = "edit"
    description = (
        "Edit a file by replacing an exact string match, inserting at a line, or appending. "
        "Returns the edited region with surrounding context so you can verify the change."
    )

    async def call(self, params: EditParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        old_string = params.old_string
        new_string = params.new_string
        insert_after_line = params.insert_after_line

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
            lines = content.split("\n")
            total_before = len(lines)

            # --- Mode 1: Insert after line number ---
            if insert_after_line is not None:
                if insert_after_line < 0 or insert_after_line > total_before:
                    return ToolError(
                        f"insert_after_line={insert_after_line} out of range (file has {total_before} lines). Use 0 to insert at the beginning.",
                        title=f"Edit {file_path}",
                        metadata={"success": False},
                    )
                insert_lines = new_string.split("\n")
                insert_pos = insert_after_line  # 0-based index after this line
                new_lines = lines[:insert_pos] + insert_lines + lines[insert_pos:]
                new_content = "\n".join(new_lines)
                Path(full).write_text(new_content, encoding="utf-8")
                snippet = _snippet_around(new_lines, insert_pos, insert_pos + len(insert_lines))
                return ToolOk(
                    f"Inserted {len(insert_lines)} line(s) after line {insert_after_line} in {file_path}\n\n{snippet}",
                    title=f"Edit {file_path}",
                    metadata={"success": True, "lines_added": len(insert_lines), "total_lines": len(new_lines)},
                )

            # --- Mode 2: Append (empty old_string) ---
            if not old_string:
                if not new_string:
                    return ToolError(
                        "Both old_string and new_string are empty. Nothing to do.",
                        title=f"Edit {file_path}",
                        metadata={"success": False},
                    )
                new_content = content + new_string
                Path(full).write_text(new_content, encoding="utf-8")
                new_lines = new_content.split("\n")
                appended_count = len(new_string.split("\n"))
                snippet = _snippet_around(new_lines, max(0, len(new_lines) - appended_count), len(new_lines))
                return ToolOk(
                    f"Appended to {file_path}\n\n{snippet}",
                    title=f"Edit {file_path}",
                    metadata={"success": True, "total_lines": len(new_lines)},
                )

            # --- Mode 3: String replacement ---
            count = content.count(old_string)
            if count == 0:
                return ToolError(
                    "old_string not found in file. Make sure it matches exactly including whitespace and indentation.",
                    title=f"Edit {file_path}",
                    metadata={"success": False},
                )
            if count > 1:
                return ToolError(
                    f"old_string found {count} times. It must be unique. Add more surrounding context to make it unique.",
                    title=f"Edit {file_path}",
                    metadata={"success": False},
                )

            # Find the line range of the replacement
            pos = content.index(old_string)
            start_line = content[:pos].count("\n")
            old_line_count = old_string.count("\n") + 1
            new_line_count = new_string.count("\n") + 1

            new_content = content.replace(old_string, new_string, 1)
            Path(full).write_text(new_content, encoding="utf-8")

            # Build snippet of the edited region
            new_lines = new_content.split("\n")
            end_line = start_line + new_line_count
            snippet = _snippet_around(new_lines, start_line, end_line)

            delta = new_line_count - old_line_count
            delta_str = f" ({'+' if delta > 0 else ''}{delta} lines)" if delta != 0 else ""
            return ToolOk(
                f"Edited {file_path}{delta_str}\n\n{snippet}",
                title=f"Edit {file_path}",
                metadata={"success": True, "total_lines": len(new_lines)},
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Edit {file_path}", metadata={"success": False})


tool = EditTool()
