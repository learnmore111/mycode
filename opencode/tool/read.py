"""Read file tool. Equivalent to src/tool/read.ts.

Enhancements:
- Large file auto-truncation with ToolResultBuilder
- 1-based line_offset for consistency with editors
- Boundary validation for line_offset/line_count
- File summary header (total lines, showing range, file size)
- Supports viewing specific line ranges efficiently
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder

# Maximum lines to return in a single read (prevents token explosion)
_MAX_LINES = 2000


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


class ReadParams(BaseModel):
    """Parameters for the read tool."""
    file_path: str = Field(description="Path to the file to read (relative to project root or absolute)")
    line_offset: int | None = Field(default=None, description="Starting line number (1-based). Use with line_count for partial reads.")
    line_count: int | None = Field(default=None, description="Number of lines to read from line_offset")


class ReadTool(CallableTool[ReadParams]):
    id = "read"
    description = "Read the contents of a file. Use line_offset and line_count for partial reads of large files."

    async def call(self, params: ReadParams, ctx: ToolContext) -> ToolResult:
        file_path = params.file_path
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path

        if not os.path.exists(full):
            return ToolError(f"File not found: {file_path}", title=f"Read {file_path}")

        if os.path.isdir(full):
            return ToolError(f"Path is a directory, not a file: {file_path}. Use listdir instead.", title=f"Read {file_path}")

        try:
            p = Path(full)
            file_size = p.stat().st_size
            content = p.read_text(encoding="utf-8", errors="replace")
            all_lines = content.split("\n")
            total = len(all_lines)

            # Determine the line range to show
            offset_0 = 0  # 0-based start index
            if params.line_offset is not None:
                # Convert 1-based to 0-based
                offset_0 = max(0, params.line_offset - 1)
                if offset_0 >= total:
                    return ToolError(
                        f"line_offset={params.line_offset} is beyond end of file ({total} lines). "
                        f"Use line_offset=1..{total}.",
                        title=f"Read {file_path}",
                        metadata={"total_lines": total},
                    )

            if params.line_count is not None:
                end = min(offset_0 + params.line_count, total)
            else:
                end = total

            selected = all_lines[offset_0:end]

            # Auto-truncate if too many lines and no explicit range requested
            truncated = False
            if len(selected) > _MAX_LINES and params.line_count is None:
                selected = selected[:_MAX_LINES]
                end = offset_0 + _MAX_LINES
                truncated = True

            # Build output with ToolResultBuilder for character-level truncation
            builder = ToolResultBuilder(max_chars=50_000)

            # File summary header
            showing_from = offset_0 + 1
            showing_to = offset_0 + len(selected)
            if showing_from == 1 and showing_to == total and not truncated:
                header = f"File: {file_path} ({total} lines, {_human_size(file_size)})"
            else:
                header = f"File: {file_path} (showing lines {showing_from}-{showing_to} of {total}, {_human_size(file_size)})"
            builder.add(header + "\n\n")

            # Numbered content
            numbered = "\n".join(
                f"{i + offset_0 + 1:6d}:{line}" for i, line in enumerate(selected)
            )
            builder.add(numbered)

            if truncated:
                builder.add(f"\n\n... truncated (showing {_MAX_LINES} of {total} lines). "
                            f"Use line_offset and line_count to view specific ranges.")

            return ToolOk(
                builder.build(),
                title=f"Read {file_path}",
                metadata={
                    "lines_shown": len(selected),
                    "total_lines": total,
                    "from_line": showing_from,
                    "to_line": showing_to,
                    "truncated": truncated or builder.truncated,
                    "file_size": file_size,
                },
            )
        except UnicodeDecodeError:
            return ToolError(
                f"Cannot read {file_path}: binary file or unsupported encoding.",
                title=f"Read {file_path}",
            )
        except Exception as e:
            return ToolError(f"Error reading file: {e}", title=f"Read {file_path}")


tool = ReadTool()
