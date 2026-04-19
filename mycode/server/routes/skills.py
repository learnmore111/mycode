"""Skills API routes."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/skill", tags=["skill"])


@router.get("")
async def list_skills():
    """List all available skills with descriptions."""
    from mycode.tool.skill import list_skills_with_descriptions, _get_search_dirs

    skills = list_skills_with_descriptions()
    search_dirs = _get_search_dirs()
    # Enrich with file path
    result = []
    for s in skills:
        path = _find_skill_path(s["name"], search_dirs)
        result.append({**s, "path": path})
    return result


@router.get("/{name}")
async def get_skill(name: str):
    """Read a skill file content."""
    from mycode.tool.skill import _get_search_dirs

    search_dirs = _get_search_dirs()
    for d in search_dirs:
        for ext in [".md", ".txt", ""]:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                content = Path(p).read_text(encoding="utf-8")
                return {"name": name, "path": p, "content": content}

    raise HTTPException(404, f"Skill '{name}' not found")


@router.delete("/{name}")
async def delete_skill(name: str):
    """Delete a skill file."""
    from mycode.tool.skill import _get_search_dirs

    search_dirs = _get_search_dirs()
    for d in search_dirs:
        for ext in [".md", ".txt", ""]:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                os.remove(p)
                return {"ok": True, "path": p}

    raise HTTPException(404, f"Skill '{name}' not found")


def _find_skill_path(name: str, search_dirs: list[str]) -> str | None:
    for d in search_dirs:
        for ext in [".md", ".txt", ""]:
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p
    return None
