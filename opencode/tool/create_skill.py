"""Create skill tool — create new skill files (.md instructions).

Features:
- Create skills in project-local or global directory
- Validates skill name and content
- Returns the created file path and usage instructions
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, atomic_write


class CreateSkillParams(BaseModel):
    """Parameters for the create_skill tool."""
    name: str = Field(description="Name of the skill (without .md extension)")
    content: str = Field(description="Skill content in markdown format. Should include a title and detailed instructions.")
    scope: str = Field(default="project", description="Where to save: 'project' (local) or 'global' (user-wide)")


class CreateSkillTool(CallableTool[CreateSkillParams]):
    id = "create_skill"
    description = (
        "Create a new skill file with custom instructions. "
        "Skills are markdown files that provide domain-specific knowledge or guidelines. "
        "Save as 'project' scope for project-specific skills, or 'global' for reusable skills across all projects."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        return False  # Creating new files is not destructive

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True  # Different skill names write to different files

    async def call(self, params: CreateSkillParams, ctx: ToolContext) -> ToolResult:
        name = params.name
        content = params.content
        scope = params.scope

        # Validate skill name
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            return ToolError(
                f"Invalid skill name: '{name}'. Use alphanumeric characters, hyphens, and underscores only.",
                title=f"Create Skill: {name}",
                metadata={"success": False},
            )

        # Validate content
        if not content.strip():
            return ToolError(
                "Skill content cannot be empty. Provide meaningful instructions in markdown format.",
                title=f"Create Skill: {name}",
                metadata={"success": False},
            )

        # Determine target directory
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        if scope == "global":
            skills_dir = Path.home() / ".opencode" / "skills"
        else:  # project
            skills_dir = Path(base) / ".opencode" / "skills"

        # Ensure directory exists
        skills_dir.mkdir(parents=True, exist_ok=True)

        # Build file path
        file_name = f"{name}.md"
        file_path = skills_dir / file_name
        full_path = str(file_path)

        try:
            existed = file_path.exists()

            # Write content atomically
            atomic_write(full_path, content)

            lines = content.count("\n") + 1
            file_size = file_path.stat().st_size

            if existed:
                msg = f"Overwrote skill '{name}' ({lines} lines, {_human_size(file_size)})"
            else:
                msg = f"Created skill '{name}' ({lines} lines, {_human_size(file_size)})"

            # Build usage instructions
            scope_desc = "project-local" if scope == "project" else "global"
            instructions = (
                f"\n\nLocation: {full_path}\n"
                f"Scope: {scope_desc}\n\n"
                f"To use this skill, call:\n"
                f"  skill(name=\"{name}\")\n\n"
                f"The skill will be automatically listed in system reminders when available."
            )

            return ToolOk(
                f"{msg}{instructions}",
                title=f"Create Skill: {name}",
                metadata={
                    "success": True,
                    "name": name,
                    "path": full_path,
                    "scope": scope,
                    "lines": lines,
                    "file_size": file_size,
                    "created": not existed,
                },
            )
        except Exception as e:
            return ToolError(f"Error creating skill: {e}", title=f"Create Skill: {name}", metadata={"success": False})


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


tool = CreateSkillTool()
