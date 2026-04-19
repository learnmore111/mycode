"""Skills API routes."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/skill", tags=["skill"])


class _CreateSkill(BaseModel):
    name: str
    content: str
    scope: str = "project"  # "project" or "global"


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


@router.post("")
async def create_skill(body: _CreateSkill):
    """Create or overwrite a skill file."""
    from mycode.project.instance import current_or_none
    from mycode.tool.base import atomic_write

    name = body.name.strip()
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, f"无效的技能名称: '{name}'，仅支持字母数字、连字符和下划线")
    if not body.content.strip():
        raise HTTPException(400, "技能内容不能为空")

    inst = current_or_none()
    base = inst.directory if inst else os.getcwd()
    skills_dir = (
        Path.home() / ".mycode" / "skills"
        if body.scope == "global"
        else Path(base) / ".mycode" / "skills"
    )
    skills_dir.mkdir(parents=True, exist_ok=True)
    file_path = skills_dir / f"{name}.md"

    try:
        atomic_write(str(file_path), body.content)
    except Exception as exc:
        raise HTTPException(500, f"写入失败: {exc}") from exc

    return {"ok": True, "name": name, "path": str(file_path), "scope": body.scope}


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
