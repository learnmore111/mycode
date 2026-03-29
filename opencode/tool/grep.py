"""Grep tool using ripgrep. Equivalent to src/tool/grep.ts."""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from opencode.project.instance import current_or_none
from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class GrepTool(ToolInfo):
    id = "grep"
    description = "Search file contents using a regex pattern. Uses ripgrep."

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Regex pattern"},
            "path": {"type": "string", "description": "Search directory"},
            "include": {"type": "string", "description": "File glob filter (e.g. '*.py')"},
        }, "required": ["pattern"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        path = args.get("path", ".")
        include = args.get("include")
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        cwd = os.path.join(base, path) if not os.path.isabs(path) else path

        rg = shutil.which("rg")
        cmd = [rg or "grep", "-rn", "--no-heading", "-m", "100"]
        if rg and include:
            cmd += ["-g", include]
        cmd.append(pattern)
        if not rg:
            cmd.append(cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd if rg else None)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace").strip()
            lines = output.count("\n") + 1 if output else 0
            if not output:
                output = "No matches found."
            return ToolResult(title=f"Grep {pattern}", output=output, metadata={"matches": lines})
        except Exception as e:
            return ToolResult(title=f"Grep {pattern}", output=f"Error: {e}", metadata={})

tool = GrepTool()
