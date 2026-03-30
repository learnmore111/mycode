"""Skill tool — load and use skill files (.md instructions). Equivalent to src/tool/skill.ts."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class SkillParams(BaseModel):
    """Parameters for the skill tool."""
    name: str = Field(description="Name of the skill to load (without .md extension)")


class SkillTool(CallableTool[SkillParams]):
    id = "skill"
    description = (
        "Load a skill file to get specialized instructions. "
        "Skills are markdown files in .opencode/skills/ that provide domain-specific knowledge."
    )

    async def call(self, params: SkillParams, ctx: ToolContext) -> ToolResult:
        name = params.name
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
                    return ToolOk(
                        content,
                        title=f"Skill: {name}",
                        metadata={"path": p, "found": True},
                    )

        return ToolError(
            f"Skill '{name}' not found. Searched in .opencode/skills/",
            title=f"Skill: {name}",
            metadata={"found": False},
        )


tool = SkillTool()
