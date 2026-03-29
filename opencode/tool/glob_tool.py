"""Glob file search tool. Equivalent to src/tool/glob.ts."""
from __future__ import annotations

import glob as globmod
import os
from typing import Any

from opencode.project.instance import current_or_none
from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class GlobTool(ToolInfo):
    id = "glob"
    description = "Find files matching a glob pattern. Returns relative file paths."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern (e.g. '**/*.py', 'src/**/*.ts')"},
                "path": {"type": "string", "description": "Directory to search in (default: project root)"},
            },
            "required": ["pattern"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        pattern = args["pattern"]
        search_path = args.get("path", "")
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        if search_path:
            base = os.path.join(base, search_path) if not os.path.isabs(search_path) else search_path
        matches = sorted(globmod.glob(pattern, root_dir=base, recursive=True))
        if len(matches) > 500:
            matches = matches[:500]
            output = "\n".join(matches) + f"\n\n... truncated (500 of {len(matches)} matches)"
        else:
            output = "\n".join(matches) if matches else "No files found."
        return ToolResult(title=f"Glob {pattern}", output=output, metadata={"count": len(matches)})

tool = GlobTool()
