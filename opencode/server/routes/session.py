"""Session API routes."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from opencode.session.prompt import PromptInput, prompt
from opencode.session.session import (
    SessionInfo,
    list_sessions,
    remove,
    set_title,
)
from opencode.session.session import create as create_session
from opencode.session.session import get as get_session

if TYPE_CHECKING:
    import asyncio

router = APIRouter(prefix="/session", tags=["session"])

# Abort signals: session_id -> asyncio.Event
_abort_signals: dict[str, asyncio.Event] = {}


def _session_json(s: SessionInfo) -> dict[str, Any]:
    return {
        "id": s.id, "slug": s.slug, "projectID": s.project_id,
        "directory": s.directory, "title": s.title, "version": s.version,
        "parentID": s.parent_id, "summary": s.summary, "share": s.share,
        "time": {"created": s.time_created, "updated": s.time_updated,
                 "compacting": s.time_compacting, "archived": s.time_archived},
    }


@router.get("")
async def session_list(directory: str = Query(default="."), limit: int = Query(default=100)):
    from opencode.project.instance import provide
    async def _fn():
        return [_session_json(s) for s in list_sessions(limit=limit)]
    return await provide(directory, _fn)


@router.post("")
async def session_create(request: Request, directory: str = Query(default=".")):
    from opencode.project.instance import provide
    body = await request.json() if request.headers.get("content-type") else {}
    async def _fn():
        s = create_session(title=body.get("title"))
        return _session_json(s)
    return await provide(directory, _fn)


@router.get("/{session_id}")
async def session_get(session_id: str, directory: str = Query(default=".")):
    from opencode.project.instance import provide
    async def _fn():
        try:
            return _session_json(get_session(session_id))
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc
    return await provide(directory, _fn)


@router.delete("/{session_id}")
async def session_delete(session_id: str, directory: str = Query(default=".")):
    from opencode.project.instance import provide
    async def _fn():
        remove(session_id)
        return {"ok": True}
    return await provide(directory, _fn)


@router.put("/{session_id}/title")
async def session_set_title(session_id: str, request: Request, directory: str = Query(default=".")):
    from opencode.project.instance import provide
    body = await request.json()
    async def _fn():
        set_title(session_id, body.get("title", ""))
        return {"ok": True}
    return await provide(directory, _fn)


@router.post("/{session_id}/message")
async def session_message(session_id: str, request: Request, directory: str = Query(default=".")):
    from opencode.bus.bus import Bus
    from opencode.project.instance import InstanceContext, ProjectInfo, set_context

    body = await request.json()
    parts = body.get("parts", [])
    model = body.get("model")
    agent = body.get("agent")
    bus = Bus()

    async def event_generator():
        project = ProjectInfo(id="global", worktree=directory)
        ctx = InstanceContext(directory=directory, worktree=directory, project=project)
        token = set_context(ctx)
        try:
            inp = PromptInput(session_id=session_id, parts=parts, model=model, agent=agent)
            async for event in prompt(inp, bus):
                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            token.reset()
            await bus.close()

    return EventSourceResponse(event_generator())


@router.post("/{session_id}/abort")
async def session_abort(session_id: str):
    """Signal an in-progress session to stop after the current tool finishes."""
    signal = _abort_signals.get(session_id)
    if signal:
        signal.set()
        return {"ok": True, "aborted": True}
    return {"ok": True, "aborted": False, "message": "No active processing for this session"}
