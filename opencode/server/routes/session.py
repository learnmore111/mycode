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


async def _build_session_context_snapshot(session_id: str) -> dict[str, Any]:
    """Rebuild a context snapshot for an existing session from persisted state."""
    from opencode.agent import agent as agentmod
    from opencode.provider import provider as providermod
    from opencode.session.context import build_context_snapshot
    from opencode.session.message import rebuild_history_from_db
    from opencode.session.system import build as build_system
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import MessageTable
    from opencode.tool import registry as tool_registry

    db = get_db_session()
    try:
        assistant_rows = (
            db.query(MessageTable)
            .filter(MessageTable.session_id == session_id, MessageTable.role == "assistant")
            .order_by(MessageTable.time_created)
            .all()
        )
    finally:
        db.close()

    last_assistant = assistant_rows[-1] if assistant_rows else None
    assistant_turns = len(assistant_rows)
    history = rebuild_history_from_db(session_id)

    if last_assistant and last_assistant.provider_id and last_assistant.model_id:
        provider_id = last_assistant.provider_id
        model_id = last_assistant.model_id
    else:
        provider_id, model_id = await providermod.default_model()

    try:
        model = await providermod.get_model(provider_id, model_id)
    except Exception:
        provider_id, model_id = await providermod.default_model()
        model = await providermod.get_model(provider_id, model_id)

    agent_name = last_assistant.agent if last_assistant and last_assistant.agent else await agentmod.default_agent()
    agent = await agentmod.get(agent_name)
    if not agent:
        agent_name = await agentmod.default_agent()
        agent = await agentmod.get(agent_name)
    if not agent:
        raise RuntimeError(f"Agent not found: {agent_name}")

    system = build_system(model=model, agent_prompt=agent.prompt, instructions=None)
    if last_assistant and last_assistant.system:
        try:
            stored_system = json.loads(last_assistant.system)
            if isinstance(stored_system, list) and stored_system:
                system = [str(item) for item in stored_system]
        except Exception:
            pass

    tool_registry.register_builtins()
    tools = tool_registry.to_llm_tools()

    actual_usage = None
    if last_assistant and any(
        value is not None
        for value in (
            last_assistant.tokens_input,
            last_assistant.tokens_output,
            last_assistant.tokens_reasoning,
            last_assistant.tokens_cache_read,
            last_assistant.tokens_cache_write,
            last_assistant.cost,
        )
    ):
        actual_usage = {
            "input_tokens": last_assistant.tokens_input or 0,
            "output_tokens": last_assistant.tokens_output or 0,
            "cache_read_tokens": last_assistant.tokens_cache_read or 0,
            "cache_write_tokens": last_assistant.tokens_cache_write or 0,
            "reasoning_tokens": last_assistant.tokens_reasoning or 0,
            "total_cost": last_assistant.cost or 0.0,
        }

    return build_context_snapshot(
        system=system,
        tools=tools if model.capabilities.toolcall else None,
        messages=history,
        model_id=f"{provider_id}/{model_id}",
        context_limit=model.limit.context if model.limit.context > 0 else 0,
        iteration=max(assistant_turns - 1, 0),
        has_history=bool(history),
        actual_usage=actual_usage,
    )


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


@router.get("/{session_id}/context")
async def session_context(session_id: str, directory: str = Query(default=".")):
    """Rebuild the current context snapshot for an existing session."""
    from opencode.project.instance import provide

    async def _fn():
        try:
            get_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc
        return await _build_session_context_snapshot(session_id)

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

    # Retrieve the shared permission manager from app state so the frontend
    # can reply to "ask" permission requests via POST /permission/:id.
    perm_manager = getattr(request.app.state, "permission_manager", None)

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
            async for event in prompt(inp, bus, history=history, permission_manager=perm_manager):
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
