"""Session API routes."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from opencode.session.prompt import PromptInput, prompt
from opencode.session.session import (
    SessionInfo,
    list_deleted,
    list_sessions,
    remove,
    restore,
    set_title,
)
from opencode.session.session import create as create_session
from opencode.session.session import get as get_session

if TYPE_CHECKING:
    import asyncio

from opencode.util import log as logmod

logger = logmod.create(service="routes.session")

router = APIRouter(prefix="/session", tags=["session"])

# Abort signals: session_id -> asyncio.Event
_abort_signals: dict[str, asyncio.Event] = {}


def get_abort_signal(session_id: str) -> asyncio.Event | None:
    """Get the abort signal for a session, if any."""
    return _abort_signals.get(session_id)


def set_abort_signal(session_id: str, event: asyncio.Event) -> None:
    """Register an abort signal for a session."""
    _abort_signals[session_id] = event
    logger.debug("abort signal registered", session_id=session_id)


def clear_abort_signal(session_id: str) -> None:
    """Remove the abort signal for a session."""
    removed = _abort_signals.pop(session_id, None)
    if removed:
        logger.debug("abort signal cleared", session_id=session_id)


def _session_json(s: SessionInfo) -> dict[str, Any]:
    return {
        "id": s.id, "slug": s.slug, "projectID": s.project_id,
        "directory": s.directory, "title": s.title, "version": s.version,
        "parentID": s.parent_id, "summary": s.summary, "share": s.share,
        "visible": s.visible,
        "time": {"created": s.time_created, "updated": s.time_updated,
                 "compacting": s.time_compacting, "archived": s.time_archived},
    }


@router.get("")
async def session_list(directory: str = Query(default="."), limit: int = Query(default=100)):
    from opencode.project.instance import provide
    async def _fn():
        return [_session_json(s) for s in list_sessions(limit=limit)]
    return await provide(directory, _fn)


@router.get("/deleted")
async def session_list_deleted(directory: str = Query(default="."), limit: int = Query(default=100)):
    """List soft-deleted sessions."""
    from opencode.project.instance import provide
    async def _fn():
        return [_session_json(s) for s in list_deleted(limit=limit)]
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


@router.post("/{session_id}/restore")
async def session_restore(session_id: str, directory: str = Query(default=".")):
    """Restore a soft-deleted session."""
    from opencode.project.instance import provide
    async def _fn():
        try:
            restore(session_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc
    return await provide(directory, _fn)


@router.put("/{session_id}/title")
async def session_set_title(session_id: str, request: Request, directory: str = Query(default=".")):
    from opencode.project.instance import provide
    body = await request.json()
    async def _fn():
        set_title(session_id, body.get("title", ""))
        return {"ok": True}
    return await provide(directory, _fn)


@router.get("/{session_id}/messages")
async def session_messages(session_id: str, directory: str = Query(default=".")):
    """Get all messages and their parts for a session."""
    from opencode.project.instance import provide
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import MessageTable, PartTable

    async def _fn():
        db = get_db_session()
        try:
            messages = (
                db.query(MessageTable)
                .filter(MessageTable.session_id == session_id)
                .order_by(MessageTable.time_created)
                .all()
            )
            if not messages:
                return []

            message_ids = [m.id for m in messages]
            parts = (
                db.query(PartTable)
                .filter(PartTable.message_id.in_(message_ids))
                .order_by(PartTable.time_created)
                .all()
            )

            parts_by_msg: dict[str, list[dict[str, Any]]] = {}
            for p in parts:
                parts_by_msg.setdefault(p.message_id, []).append({
                    "id": p.id,
                    "type": p.type,
                    "content": p.content,
                    "tool": p.tool,
                    "toolCallId": p.tool_call_id,
                    "state": p.state,
                    "time": {"created": p.time_created, "completed": p.time_completed},
                })

            result = []
            for m in messages:
                result.append({
                    "id": m.id,
                    "sessionId": m.session_id,
                    "role": m.role,
                    "parentId": m.parent_id,
                    "modelId": m.model_id,
                    "providerId": m.provider_id,
                    "agent": m.agent,
                    "tokens": {
                        "input": m.tokens_input,
                        "output": m.tokens_output,
                        "reasoning": m.tokens_reasoning,
                        "cacheRead": m.tokens_cache_read,
                        "cacheWrite": m.tokens_cache_write,
                    },
                    "cost": m.cost,
                    "error": json.loads(m.error) if m.error else None,
                    "parts": parts_by_msg.get(m.id, []),
                    "time": {"created": m.time_created, "completed": m.time_completed},
                })
            return result
        finally:
            db.close()

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
        import asyncio as _aio
        project = ProjectInfo(id="global", worktree=directory)
        ctx = InstanceContext(directory=directory, worktree=directory, project=project)
        token = set_context(ctx)
        abort_event = _aio.Event()
        set_abort_signal(session_id, abort_event)
        try:
            # Rebuild conversation history from DB so the model sees prior turns
            from opencode.session.message import rebuild_history_from_db
            history = rebuild_history_from_db(session_id)

            inp = PromptInput(session_id=session_id, parts=parts, model=model, agent=agent)
            async for event in prompt(inp, bus, history=history):
                yield {"event": event.type, "data": json.dumps(event.data)}
        except _aio.CancelledError:
            logger.debug("SSE stream cancelled by client", session_id=session_id)
        finally:
            token.reset()
            await bus.close()
            clear_abort_signal(session_id)

    return EventSourceResponse(event_generator())


@router.post("/{session_id}/abort")
async def session_abort(session_id: str):
    """Signal an in-progress session to stop after the current tool finishes."""
    signal = get_abort_signal(session_id)
    if signal:
        signal.set()
        return {"ok": True, "aborted": True}
    return {"ok": True, "aborted": False, "message": "No active processing for this session"}


@router.get("/{session_id}/compaction-events")
async def session_compaction_events(session_id: str, directory: str = Query(default=".")):
    """Get all compaction events for a session.

    Returns a list of compaction events with metrics and summaries of old messages.
    This allows users to see what was compressed during the session.
    """
    async def _fn():
        from opencode.session.message import get_compaction_events
        return get_compaction_events(session_id)

    return await _fn()
