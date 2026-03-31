"""Glob file search tool. Equivalent to src/tool/glob.ts."""
from __future__ import annotations

import glob as globmod
import os

from pydantic import BaseModel, Field

from opencode.file.ignore import should_ignore_path
from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolOk, ToolResult


class GlobParams(BaseModel):
    """Parameters for the glob tool."""
    pattern: str = Field(description="Glob pattern (e.g. '**/*.py', 'src/**/*.ts')")
    path: str = Field(default="", description="Directory to search in (default: project root)")


class GlobTool(CallableTool[GlobParams]):
    id = "glob"
    description = "Find files matching a glob pattern. Returns relative file paths."

    async def call(self, params: GlobParams, ctx: ToolContext) -> ToolResult:
        pattern = params.pattern
        search_path = params.path
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        if search_path:
            base = os.path.join(base, search_path) if not os.path.isabs(search_path) else search_path
        raw_matches = sorted(globmod.glob(pattern, root_dir=base, recursive=True))
        # Filter out ignored directories (.venv, __pycache__, node_modules, etc.)
        matches = [m for m in raw_matches if not should_ignore_path(m)]
        total_count = len(matches)
        if total_count > 500:
            matches = matches[:500]
            output = "\n".join(matches) + f"\n\n... truncated (showing 500 of {total_count} matches)"
        else:
            output = "\n".join(matches) if matches else "No files found."
        return ToolOk(output, title=f"Glob {pattern}", metadata={"count": total_count})


tool = GlobTool()
