"""Skill tool — load and use skill files (.md instructions). Equivalent to src/tool/skill.ts.

Enhanced with:
- User home directory skill search (~/.opencode/skills/)
- Lists available skills when name not found
"""
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

    def is_read_only(self, args=None) -> bool:
        return True

    def is_concurrency_safe(self, args=None) -> bool:
        return True

    async def call(self, params: SkillParams, ctx: ToolContext) -> ToolResult:
        name = params.name
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        # Search directories: project-local + user home
        search_dirs = [
            os.path.join(base, ".opencode", "skills"),
            os.path.join(base, ".opencode", "skill"),
            os.path.join(Path.home(), ".opencode", "skills"),
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

        # Not found — list available skills as hint
        available = _list_available_skills(search_dirs)
        hint = ""
        if available:
            hint = f"\n\nAvailable skills: {', '.join(sorted(available))}"

        return ToolError(
            f"Skill '{name}' not found. Searched in .opencode/skills/ and ~/.opencode/skills/{hint}",
            title=f"Skill: {name}",
            metadata={"found": False, "available": sorted(available) if available else []},
        )


def _list_available_skills(search_dirs: list[str]) -> set[str]:
    """List all available skill names across search directories."""
    skills: set[str] = set()
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if os.path.isfile(fp):
                name = f
                for ext in [".md", ".txt"]:
                    if name.endswith(ext):
                        name = name[:-len(ext)]
                        break
                if name:
                    skills.add(name)
    return skills


tool = SkillTool()
