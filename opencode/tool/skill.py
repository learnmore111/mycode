"""Skill tool — load and use skill files (.md instructions). Equivalent to src/tool/skill.ts."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any
from opencode.tool.base import ToolInfo, ToolResult, ToolContext
from opencode.project.instance import current_or_none


class SkillTool(ToolInfo):
    id = "skill"
    description = (
        "Load a skill file to get specialized instructions. "
        "Skills are markdown files in .opencode/skills/ that provide domain-specific knowledge."
    )

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the skill to load (without .md extension)"},
            },
            "required": ["name"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = args["name"]
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        search_dirs = [
            os.path.join(base, ".opencode", "skills"),
            os.path.join(base, ".opencode", "skill"),
        ]

        for d in search_dirs:
            for ext in [".md", ".txt", ""]:
                p = os.path.join(d, name + ext)
                if os.path.isfile(p):
                    content = Path(p).read_text(encoding="utf-8")
                    return ToolResult(
                        title=f"Skill: {name}",
                        output=content,
                        metadata={"path": p, "found": True},
                    )

        return ToolResult(
            title=f"Skill: {name}",
            output=f"Skill '{name}' not found. Searched in .opencode/skills/",
            metadata={"found": False},
        )


tool = SkillTool()
