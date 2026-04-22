"""Skill tool — load and use skill files (.md instructions).

Features:
- User home directory skill search (~/.mycode/skills/)
- Recursive subdirectory scanning (e.g. gsd/agents/gsd-code-fixer)
- Lists available skills when name not found
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mycode.project.instance import current_or_none
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult


class SkillParams(BaseModel):
    """Parameters for the skill tool."""
    name: str = Field(description="Name of the skill to load (without .md extension). Supports namespace paths like 'gsd/agents/gsd-code-fixer'.")


class SkillTool(CallableTool[SkillParams]):
    id = "skill"
    description = (
        "Load a skill file to get specialized instructions. "
        "Skills are markdown files in .mycode/skills/ that provide domain-specific knowledge. "
        "Supports nested directories (e.g. 'gsd/agents/gsd-code-fixer')."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: SkillParams, ctx: ToolContext) -> ToolResult:
        name = params.name
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        # Search directories: project-local + user home
        search_dirs = [
            os.path.join(base, ".mycode", "skills"),
            os.path.join(base, ".mycode", "skill"),
            os.path.join(str(Path.home()), ".mycode", "skills"),
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
            f"Skill '{name}' not found. Searched in .mycode/skills/ and ~/.mycode/skills/{hint}",
            title=f"Skill: {name}",
            metadata={"found": False, "available": sorted(available) if available else []},
        )


def _iter_skill_files(directory: str) -> list[tuple[str, str]]:
    """Recursively iterate skill files under *directory*.

    Returns a list of ``(relative_name, absolute_path)`` pairs.
    ``relative_name`` uses ``/`` separators and has no extension,
    e.g. ``"gsd/agents/gsd-code-fixer"``.
    """
    results: list[tuple[str, str]] = []
    base = Path(directory)
    if not base.is_dir():
        return results

    for root, _dirs, files in os.walk(directory):
        for f in sorted(files):
            fp = os.path.join(root, f)
            if not os.path.isfile(fp):
                continue
            # Compute name: strip extension and make relative
            name = f
            for ext in [".md", ".txt"]:
                if name.endswith(ext):
                    name = name[: -len(ext)]
                    break
            if not name:
                continue
            # Build relative path from search dir root
            rel_dir = os.path.relpath(root, directory)
            rel_name = name if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/" + name
            results.append((rel_name, fp))
    return results


def _list_available_skills(search_dirs: list[str]) -> set[str]:
    """List all available skill names across search directories."""
    skills: set[str] = set()
    for d in search_dirs:
        for name, _path in _iter_skill_files(d):
            skills.add(name)
    return skills


def _get_search_dirs() -> list[str]:
    """Return the standard skill search directories."""
    inst = current_or_none()
    base = inst.directory if inst else os.getcwd()
    return [
        os.path.join(base, ".mycode", "skills"),
        os.path.join(base, ".mycode", "skill"),
        os.path.join(str(Path.home()), ".mycode", "skills"),
    ]


def list_skills_with_descriptions() -> list[dict[str, str]]:
    """Scan skill directories and return [{name, description}] for each skill.

    Description is extracted from the first non-empty line of the file.
    Results are sorted by name for cache stability.
    """
    search_dirs = _get_search_dirs()
    # Use dict to deduplicate (first match wins, same as tool lookup order)
    skills: dict[str, str] = {}
    for d in search_dirs:
        for name, fp in _iter_skill_files(d):
            if name in skills:
                continue
            # Extract first non-empty line as description
            description = ""
            try:
                with open(fp, encoding="utf-8") as fh:
                    for line in fh:
                        stripped = line.strip()
                        if stripped:
                            description = stripped
                            break
            except Exception:
                description = ""
            skills[name] = description
    return [{"name": n, "description": skills[n]} for n in sorted(skills)]


tool = SkillTool()
