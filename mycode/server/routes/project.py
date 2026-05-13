"""Project API routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from mycode.project.instance import current_or_none
from mycode.project.project import from_directory
from mycode.util import log as logmod

logger = logmod.create(service="routes.project")

router = APIRouter(prefix="/project", tags=["project"])


@router.get("")
async def project_get(directory: str = Query(default=".")) -> Any:
    """Get project info for a directory."""
    logger.info("project lookup requested", directory=directory)
    try:
        project = await from_directory(directory)
        resolved_directory = str(Path(directory).expanduser().resolve())
        logger.info(
            "project lookup resolved",
            requested_directory=directory,
            resolved_directory=resolved_directory,
            project_id=project.id,
            worktree=project.worktree,
        )
        return {
            "id": project.id,
            "directory": resolved_directory,
            "name": Path(resolved_directory).name or resolved_directory,
            "worktree": project.worktree,
        }
    except Exception as e:
        logger.error("project lookup failed", directory=directory, error=str(e))
        return {"error": str(e)}


@router.get("/current")
async def project_current() -> dict[str, Any]:
    """Get the current project context."""
    inst = current_or_none()
    if not inst:
        logger.debug("project current requested with no active context")
        return {"active": False}
    logger.debug(
        "project current requested",
        directory=inst.directory,
        worktree=inst.worktree,
        project_id=inst.project.id,
    )
    return {
        "active": True,
        "directory": inst.directory,
        "worktree": inst.worktree,
        "project_id": inst.project.id,
    }
