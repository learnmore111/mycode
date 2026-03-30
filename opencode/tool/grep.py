"""Grep tool using ripgrep. Equivalent to src/tool/grep.ts."""
from __future__ import annotations

import asyncio
import os
import shutil

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class GrepParams(BaseModel):
    """Parameters for the grep tool."""
    pattern: str = Field(description="Regex pattern")
    path: str = Field(default=".", description="Search directory")
    include: str | None = Field(default=None, description="File glob filter (e.g. '*.py')")


class GrepTool(CallableTool[GrepParams]):
    id = "grep"
    description = "Search file contents using a regex pattern. Uses ripgrep."

    async def call(self, params: GrepParams, ctx: ToolContext) -> ToolResult:
        pattern = params.pattern
        path = params.path
        include = params.include
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        cwd = os.path.join(base, path) if not os.path.isabs(path) else path

        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "-rn", "--no-heading", "-m", "100"]
            if include:
                cmd += ["-g", include]
            cmd.append(pattern)
            cmd.append(".")
            exec_cwd = cwd
        else:
            cmd = ["grep", "-rn", "-m", "100", pattern, cwd]
            exec_cwd = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=exec_cwd)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace").strip()
            lines = output.count("\n") + 1 if output else 0
            if not output:
                output = "No matches found."
            return ToolOk(output, title=f"Grep {pattern}", metadata={"matches": lines})
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Grep {pattern}")


tool = GrepTool()
