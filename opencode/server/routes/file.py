"""File API routes."""
from __future__ import annotations

from fastapi import APIRouter, Query

from opencode.file import file as filemod
from opencode.project.instance import InstanceContext, ProjectInfo, set_context

router = APIRouter(prefix="/file", tags=["file"])


def _ensure_ctx(directory: str):
    project = ProjectInfo(id="global", worktree=directory)
    ctx = InstanceContext(directory=directory, worktree=directory, project=project)
    return ctx, set_context(ctx)


@router.get("")
async def file_read(path: str = Query(...), directory: str = Query(default=".")):
    _ctx, token = _ensure_ctx(directory)
    try:
        return await filemod.read(path)
    finally:
        token.reset()


@router.get("/list")
async def file_list(path: str = Query(default=None), directory: str = Query(default=".")):
    _ctx, token = _ensure_ctx(directory)
    try:
        return await filemod.list_dir(path)
    finally:
        token.reset()


@router.get("/search")
async def file_search(query: str = Query(...), limit: int = Query(default=50), directory: str = Query(default=".")):
    _ctx, token = _ensure_ctx(directory)
    try:
        return await filemod.search(query, limit=limit)
    finally:
        token.reset()
