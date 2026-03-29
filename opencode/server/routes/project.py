"""Project API routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from opencode.project.instance import current_or_none
from opencode.project.project import from_directory

router = APIRouter(prefix="/project", tags=["project"])


@router.get("")
async def project_get(directory: str = Query(default=".")):
    """Get project info for a directory."""
    try:
        project = await from_directory(directory)
        return {
            "id": project.id,
            "worktree": project.worktree,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/current")
async def project_current() -> dict[str, Any]:
    """Get the current project context."""
    inst = current_or_none()
    if not inst:
        return {"active": False}
    return {
        "active": True,
        "directory": inst.directory,
        "worktree": inst.worktree,
        "project_id": inst.project.id,
    }
