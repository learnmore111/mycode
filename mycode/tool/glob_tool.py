"""Glob file search tool.

Features:
- Returns file type breakdown summary (e.g. 10 .py, 3 .ts)
- ToolResultBuilder for output truncation control
- Clearer truncation messages with actionable advice
"""
from __future__ import annotations

import glob as globmod
import os
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from mycode.file.ignore import should_ignore_path
from mycode.project.instance import current_or_none
from mycode.tool.base import CallableTool, ToolContext, ToolOk, ToolResult, ToolResultBuilder, validate_path_safety

_MAX_RESULTS = 500


class GlobParams(BaseModel):
    """Parameters for the glob tool."""
    pattern: str = Field(description="Glob pattern (e.g. '**/*.py', 'src/**/*.ts')")
    path: str = Field(default="", description="Directory to search in (default: project root)")


class GlobTool(CallableTool[GlobParams]):
    id = "glob"
    description = "Find files matching a glob pattern. Returns relative file paths with a summary."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: GlobParams, ctx: ToolContext) -> ToolResult:
        pattern = params.pattern
        search_path = params.path
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        if search_path:
            resolved_search = os.path.join(base, search_path) if not os.path.isabs(search_path) else search_path
            path_error = validate_path_safety(resolved_search, base)
            if path_error:
                return ToolOk(
                    path_error,
                    title=f"Glob {pattern}",
                    metadata={"count": 0, "pattern": pattern},
                )
            base = resolved_search
        raw_matches = sorted(globmod.glob(pattern, root_dir=base, recursive=True))
        matches = [m for m in raw_matches if not should_ignore_path(m)]
        total_count = len(matches)

        if total_count == 0:
            return ToolOk(
                f"No files found matching pattern: {pattern}",
                title=f"Glob {pattern}",
                metadata={"count": 0, "pattern": pattern},
            )

        # File type breakdown
        ext_counts = Counter(os.path.splitext(m)[1] or "(no ext)" for m in matches)
        top_exts = ext_counts.most_common(8)
        ext_summary = ", ".join(f"{count} {ext}" for ext, count in top_exts)
        if len(ext_counts) > 8:
            ext_summary += f", ... ({len(ext_counts) - 8} more types)"

        builder = ToolResultBuilder(max_chars=50_000)
        builder.add(f"Found {total_count} file(s) matching '{pattern}' [{ext_summary}]\n\n")

        truncated = total_count > _MAX_RESULTS
        display_matches = matches[:_MAX_RESULTS] if truncated else matches
        builder.add("\n".join(display_matches))

        if truncated:
            builder.add(f"\n\n... showing {_MAX_RESULTS} of {total_count} matches. "
                        "Use a more specific pattern to narrow results.")

        return ToolOk(
            builder.build(),
            title=f"Glob {pattern}",
            metadata={
                "count": total_count,
                "pattern": pattern,
                "truncated": truncated or builder.truncated,
                "extensions": dict(ext_counts.most_common(10)),
            },
        )


tool = GlobTool()
