"""Session API routes."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from mycode.session.prompt import PromptInput, prompt
from mycode.session.session import (
    PausedRunInfo,
    SessionInfo,
    clear_paused_run,
    get_paused_run,
    list_deleted,
    list_sessions,
    remove,
    restore,
    set_paused_run,
    set_title,
)
from mycode.session.session import create as create_session
from mycode.session.session import get as get_session

if TYPE_CHECKING:
    import asyncio

from mycode.util import log as logmod

logger = logmod.create(service="routes.session")

router = APIRouter(prefix="/session", tags=["session"])

_MUTATING_TOOLS = frozenset({"edit", "write"})
_TOOL_TARGET_PATTERNS = [
    re.compile(r"^Edited\s+(.+?)(?:\s+\(|$)", re.MULTILINE),
    re.compile(r"^Overwrote\s+(.+?)(?:\s+\(|$)", re.MULTILINE),
    re.compile(r"^Created\s+(.+?)(?:\s+\(|$)", re.MULTILINE),
    re.compile(r"^Appended to\s+(.+?)(?:\s+\(|$)", re.MULTILINE),
    re.compile(r"^Inserted\s+\d+\s+line\(s\)\s+after\s+line\s+\d+\s+in\s+(.+?)(?:\s+\(|$)", re.MULTILINE),
]

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
        "id": s.id,
        "slug": s.slug,
        "projectID": s.project_id,
        "directory": s.directory,
        "title": s.title,
        "version": s.version,
        "parentID": s.parent_id,
        "summary": s.summary,
        "share": s.share,
        "visible": s.visible,
        "time": {
            "created": s.time_created,
            "updated": s.time_updated,
            "compacting": s.time_compacting,
            "archived": s.time_archived,
        },
    }


def _paused_run_json(info: PausedRunInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "sessionId": info.session_id,
        "lastUserText": info.last_user_text,
        "partialText": info.partial_text,
        "pausedAt": info.paused_at,
        "model": info.model,
        "agent": info.agent,
    }


def _build_resume_prompt(last_user_text: str, partial_text: str | None = None) -> str:
    sections = [
        "继续处理我上一个被暂停的请求。",
        f"上一个请求：{last_user_text}",
    ]

    if partial_text and partial_text.strip():
        sections.append(f"暂停前你已经输出了部分内容：{partial_text.strip()[:400]}")

    sections.append("请先检查当前会话历史和工作区里已经完成的代码修改，再从中断处继续，不要重复已经做完的步骤。")
    return "\n\n".join(sections)


def _extract_tool_target_file(text: str) -> str | None:
    for pattern in _TOOL_TARGET_PATTERNS:
        match = pattern.search(text)
        value = match.group(1).strip() if match else ""
        if value:
            return value
    return None


def _normalize_summary_file(diff: Any) -> str | None:
    if isinstance(diff, str):
        return diff or None
    if isinstance(diff, dict):
        for key in ("file", "path", "label"):
            value = diff.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _collect_session_code_changes(session_id: str, limit: int = 6) -> list[dict[str, Any]]:
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import PartTable

    changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    db = get_db_session()
    try:
        parts = (
            db.query(PartTable)
            .filter(PartTable.session_id == session_id, PartTable.type == "tool")
            .order_by(PartTable.time_completed.desc().nullslast(), PartTable.time_created.desc())
            .all()
        )
    finally:
        db.close()

    for part in parts:
        if not part.tool or part.tool not in _MUTATING_TOOLS:
            continue

        state = part.state or {}
        output = str(state.get("output") or part.content or "")
        file_path = _extract_tool_target_file(output)
        key = f"{part.tool}:{file_path or part.id}"
        if key in seen:
            continue
        seen.add(key)

        changes.append({
            "id": key,
            "tool": part.tool,
            "filePath": file_path,
            "time": part.time_completed or part.time_created,
            "preview": " ".join(output.splitlines()[:2]).strip() or None,
        })
        if len(changes) >= limit:
            return changes

    if changes:
        return changes

    session_info = get_session(session_id)
    diffs = session_info.summary.get("diffs") if session_info.summary else None
    if not isinstance(diffs, list):
        return changes

    for diff in diffs:
        file_path = _normalize_summary_file(diff)
        if not file_path:
            continue
        changes.append({
            "id": f"summary:{file_path}",
            "tool": "summary",
            "filePath": file_path,
            "time": 0,
            "preview": "来自会话改动摘要",
        })
        if len(changes) >= limit:
            break

    return changes


async def _build_session_context_snapshot(session_id: str) -> dict[str, Any]:
    """Rebuild a context snapshot for an existing session from persisted state."""
    from mycode.agent import agent as agentmod
    from mycode.provider import provider as providermod
    from mycode.session.context import build_context_snapshot
    from mycode.session.message import rebuild_history_from_db
    from mycode.session.system import build as build_system
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable
    from mycode.tool import registry as tool_registry

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


def _stream_session_prompt(
    session_id: str,
    directory: str,
    *,
    parts: list[dict[str, Any]],
    model: str | None = None,
    agent: str | None = None,
    clear_pause_before_start: bool = False,
) -> EventSourceResponse:
    from mycode.bus.bus import Bus
    from mycode.project.instance import InstanceContext, ProjectInfo, set_context

    bus = Bus()

    async def event_generator():
        import asyncio as _aio
        from mycode.session.message import rebuild_history_from_db

        project = ProjectInfo(id="global", worktree=directory)
        ctx = InstanceContext(directory=directory, worktree=directory, project=project)
        token = set_context(ctx)
        abort_event = _aio.Event()
        set_abort_signal(session_id, abort_event)
        try:
            if clear_pause_before_start:
                clear_paused_run(session_id)

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


@router.get("")
async def session_list(directory: str = Query(default="."), limit: int = Query(default=100)):
    from mycode.project.instance import provide

    async def _fn():
        return [_session_json(s) for s in list_sessions(limit=limit)]

    return await provide(directory, _fn)


@router.get("/deleted")
async def session_list_deleted(directory: str = Query(default="."), limit: int = Query(default=100)):
    """List soft-deleted sessions."""
    from mycode.project.instance import provide

    async def _fn():
        return [_session_json(s) for s in list_deleted(limit=limit)]

    return await provide(directory, _fn)


@router.post("")
async def session_create(request: Request, directory: str = Query(default=".")):
    from mycode.project.instance import provide

    body = await request.json() if request.headers.get("content-type") else {}

    async def _fn():
        s = create_session(title=body.get("title"))
        return _session_json(s)

    return await provide(directory, _fn)


@router.get("/{session_id}")
async def session_get(session_id: str, directory: str = Query(default=".")):
    from mycode.project.instance import provide

    async def _fn():
        try:
            return _session_json(get_session(session_id))
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc

    return await provide(directory, _fn)


@router.delete("/{session_id}")
async def session_delete(session_id: str, directory: str = Query(default=".")):
    from mycode.project.instance import provide

    async def _fn():
        remove(session_id)
        return {"ok": True}

    return await provide(directory, _fn)


@router.post("/{session_id}/restore")
async def session_restore(session_id: str, directory: str = Query(default=".")):
    """Restore a soft-deleted session."""
    from mycode.project.instance import provide

    async def _fn():
        try:
            restore(session_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc

    return await provide(directory, _fn)


@router.put("/{session_id}/title")
async def session_set_title(session_id: str, request: Request, directory: str = Query(default=".")):
    from mycode.project.instance import provide

    body = await request.json()

    async def _fn():
        set_title(session_id, body.get("title", ""))
        return {"ok": True}

    return await provide(directory, _fn)


@router.get("/{session_id}/context")
async def session_context(session_id: str, directory: str = Query(default=".")):
    """Rebuild the current context snapshot for an existing session."""
    from mycode.project.instance import provide

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
    from mycode.project.instance import provide
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable, PartTable

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
                # Hide system-reminder messages from UI chat view.
                # They are user messages injected by the agentic loop for
                # skills/memory/date context; visible only in ContextViewer.
                if m.role == "user":
                    msg_parts = parts_by_msg.get(m.id, [])
                    if msg_parts and all(
                        "<system-reminder>" in (p.get("content") or "")
                        for p in msg_parts
                        if p.get("type") == "text"
                    ):
                        continue

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


@router.get("/{session_id}/changes")
async def session_changes(
    session_id: str,
    directory: str = Query(default="."),
    limit: int = Query(default=6, ge=1, le=50),
):
    """Return the most recent code changes for a session."""
    from mycode.project.instance import provide

    async def _fn():
        try:
            get_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc
        return _collect_session_code_changes(session_id, limit=limit)

    return await provide(directory, _fn)


@router.get("/{session_id}/pause")
async def session_pause_get(session_id: str, directory: str = Query(default=".")):
    """Get the persisted paused state for a session, if any."""
    from mycode.project.instance import provide

    async def _fn():
        try:
            get_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc

        state = get_paused_run(session_id)
        return {"paused": state is not None, "state": _paused_run_json(state)}

    return await provide(directory, _fn)


@router.post("/{session_id}/pause")
async def session_pause_set(session_id: str, request: Request, directory: str = Query(default=".")):
    """Persist pause metadata for a session and abort the current run if needed."""
    from mycode.project.instance import provide

    body = await request.json() if request.headers.get("content-type") else {}

    async def _fn():
        try:
            get_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc

        last_user_text = str(body.get("lastUserText") or "").strip()
        partial_text = str(body.get("partialText") or "").strip() or None
        model = body.get("model")
        agent = body.get("agent")
        paused_at_raw = body.get("pausedAt")
        paused_at = paused_at_raw if isinstance(paused_at_raw, int) else None

        state = None
        if last_user_text:
            state = set_paused_run(
                session_id,
                last_user_text=last_user_text,
                partial_text=partial_text,
                model=model if isinstance(model, str) else None,
                agent=agent if isinstance(agent, str) else None,
                paused_at=paused_at,
            )

        signal = get_abort_signal(session_id)
        aborted = False
        if signal:
            signal.set()
            aborted = True

        return {
            "ok": True,
            "aborted": aborted,
            "paused": state is not None,
            "state": _paused_run_json(state),
        }

    return await provide(directory, _fn)


@router.delete("/{session_id}/pause")
async def session_pause_clear(session_id: str, directory: str = Query(default=".")):
    """Clear the persisted paused state for a session."""
    from mycode.project.instance import provide

    async def _fn():
        try:
            get_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc
        clear_paused_run(session_id)
        return {"ok": True}

    return await provide(directory, _fn)


@router.post("/{session_id}/message")
async def session_message(session_id: str, request: Request, directory: str = Query(default=".")):
    body = await request.json()
    parts = body.get("parts", [])
    model = body.get("model")
    agent = body.get("agent")
    return _stream_session_prompt(session_id, directory, parts=parts, model=model, agent=agent)



@router.post("/{session_id}/resume")
async def session_resume(session_id: str, directory: str = Query(default=".")):
    """Resume a previously paused session by replaying the stored continuation prompt."""
    from mycode.project.instance import provide

    async def _fn():
        try:
            get_session(session_id)
        except KeyError as exc:
            raise HTTPException(404, f"Session not found: {session_id}") from exc

        state = get_paused_run(session_id)
        if state is None:
            raise HTTPException(409, f"Session {session_id} has no paused state")
        return state

    paused = await provide(directory, _fn)
    parts = [{"type": "text", "content": _build_resume_prompt(paused.last_user_text, paused.partial_text)}]
    return _stream_session_prompt(
        session_id,
        directory,
        parts=parts,
        model=paused.model,
        agent=paused.agent,
        clear_pause_before_start=True,
    )


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
        from mycode.session.message import get_compaction_events

        return get_compaction_events(session_id)

    return await _fn()
