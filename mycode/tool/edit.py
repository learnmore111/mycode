"""Edit file tool — string replacement based editing.

Features:
- Path safety validation (prevent editing outside project directory)
- Atomic write (temp file + rename to prevent corruption)
- Uniqueness check shows all match locations (line numbers) on failure
- No-op detection (old_string == new_string)
- Richer post-edit snippet with clear change markers
- insert_after_line for line-based insertion
- File not found suggests using write tool
- Capability declarations (is_concurrency_safe=False)
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
    atomic_write,
    resolve_tool_path,
)

_CONTEXT_LINES = 4
_MAX_CONTENT_SIZE = 10 * 1024 * 1024  # 10 MB limit for edit operations


def _snippet_around(lines: list[str], start: int, end: int, context: int = _CONTEXT_LINES) -> str:
    """Return a numbered snippet around the [start, end) line range."""
    lo = max(0, start - context)
    hi = min(len(lines), end + context)
    parts: list[str] = []
    for i in range(lo, hi):
        marker = " " if i < start or i >= end else "|"
        parts.append(f"{i + 1:6d}{marker}{lines[i]}")
    return "\n".join(parts)


def _find_all_occurrences(content: str, needle: str) -> list[int]:
    """Return 1-based line numbers of all occurrences of needle in content."""
    positions: list[int] = []
    start = 0
    while True:
        idx = content.find(needle, start)
        if idx == -1:
            break
        line_no = content[:idx].count("\n") + 1
        positions.append(line_no)
        start = idx + 1
    return positions


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

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        return False  # Edits are reversible (can be undone with another edit)

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return False  # File edits are not concurrency-safe

    async def call(self, params: EditParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        old_string = params.old_string
        new_string = params.new_string
        insert_after_line = params.insert_after_line

        # Size limit to prevent DoS
        if len(new_string) > _MAX_CONTENT_SIZE:
            return ToolError(
                f"new_string too large: {len(new_string)} bytes exceeds {_MAX_CONTENT_SIZE // (1024 * 1024)}MB limit.",
                title=f"Edit {file_path}",
                metadata={"success": False},
            )

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        full, path_error = resolve_tool_path(file_path, base)
        if path_error:
            return ToolError(path_error, title=f"Edit {file_path}", metadata={"success": False})

        if not os.path.exists(full):
            return ToolError(
                f"File not found: {file_path}. Use the write tool to create new files.",
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
                        f"insert_after_line={insert_after_line} out of range (file has {total_before} lines). "
                        f"Valid range: 0..{total_before}. Use 0 to insert at the beginning.",
                        title=f"Edit {file_path}",
                        metadata={"success": False, "total_lines": total_before},
                    )
                insert_lines = new_string.split("\n")
                insert_pos = insert_after_line
                new_lines = lines[:insert_pos] + insert_lines + lines[insert_pos:]
                new_content = "\n".join(new_lines)
                atomic_write(full, new_content)
                snippet = _snippet_around(new_lines, insert_pos, insert_pos + len(insert_lines))
                return ToolOk(
                    f"Inserted {len(insert_lines)} line(s) after line {insert_after_line} in {file_path} "
                    f"({total_before} → {len(new_lines)} lines)\n\n{snippet}",
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
                atomic_write(full, new_content)
                new_lines = new_content.split("\n")
                appended_count = len(new_string.split("\n"))
                snippet = _snippet_around(new_lines, max(0, len(new_lines) - appended_count), len(new_lines))
                return ToolOk(
                    f"Appended to {file_path} ({total_before} → {len(new_lines)} lines)\n\n{snippet}",
                    title=f"Edit {file_path}",
                    metadata={"success": True, "total_lines": len(new_lines)},
                )

            # --- Mode 3: String replacement ---
            if old_string == new_string:
                return ToolError(
                    "old_string and new_string are identical. No changes needed.",
                    title=f"Edit {file_path}",
                    metadata={"success": False},
                )

            count = content.count(old_string)
            if count == 0:
                hint = ""
                stripped_old = old_string.strip()
                if stripped_old and stripped_old != old_string:
                    stripped_count = content.count(stripped_old)
                    if stripped_count > 0:
                        locs = _find_all_occurrences(content, stripped_old)
                        hint = (f"\nHint: A stripped version was found {stripped_count} time(s) at line(s) "
                                f"{', '.join(str(ln) for ln in locs[:10])}. "
                                f"Check leading/trailing whitespace.")
                if not hint and old_string.lower() in content.lower():
                    hint = "\nHint: A case-insensitive match exists. Check exact casing."
                return ToolError(
                    f"old_string not found in file. Make sure it matches exactly "
                    f"including whitespace and indentation.{hint}",
                    title=f"Edit {file_path}",
                    metadata={"success": False, "total_lines": total_before},
                )
            if count > 1:
                locations = _find_all_occurrences(content, old_string)
                loc_str = ", ".join(str(ln) for ln in locations[:20])
                return ToolError(
                    f"old_string found {count} times at line(s): {loc_str}. "
                    f"It must be unique — include more surrounding lines to disambiguate.",
                    title=f"Edit {file_path}",
                    metadata={"success": False, "match_count": count, "match_lines": locations[:20]},
                )

            pos = content.index(old_string)
            start_line = content[:pos].count("\n")
            old_line_count = old_string.count("\n") + 1
            new_line_count = new_string.count("\n") + 1

            new_content = content.replace(old_string, new_string, 1)
            atomic_write(full, new_content)

            new_lines = new_content.split("\n")
            end_line = start_line + new_line_count
            snippet = _snippet_around(new_lines, start_line, end_line)

            delta = new_line_count - old_line_count
            delta_str = f" ({'+' if delta > 0 else ''}{delta} lines)" if delta != 0 else ""
            return ToolOk(
                f"Edited {file_path}{delta_str} ({total_before} → {len(new_lines)} lines)\n\n{snippet}",
                title=f"Edit {file_path}",
                metadata={
                    "success": True,
                    "total_lines": len(new_lines),
                    "changed_range": [start_line + 1, end_line],
                },
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Edit {file_path}", metadata={"success": False})


tool = EditTool()
