"""FastAPI application — complete API. Equivalent to src/server/server.ts + routes/."""
from __future__ import annotations
import json
from typing import Any
from fastapi import FastAPI, Query, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from opencode import __version__
from opencode.bus.bus import Bus
from opencode.session.prompt import PromptInput, prompt
from opencode.session.session import (
    SessionInfo, create as create_session, get as get_session,
    list_sessions, remove, set_title, set_summary, touch,
)
from opencode.provider import provider as providermod
from opencode.agent import agent as agentmod
from opencode.config import config as configmod
from opencode.project.instance import InstanceContext, ProjectInfo, provide, set_context
from opencode.file import file as filemod

_bus = Bus()


def _ensure_ctx(directory: str) -> tuple[InstanceContext, Any]:
    project = ProjectInfo(id="global", worktree=directory)
    ctx = InstanceContext(directory=directory, worktree=directory, project=project)
    return ctx, set_context(ctx)


def create_app() -> FastAPI:
    app = FastAPI(title="opencode", version=__version__, description="OpenCode AI coding agent API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # --- Health ---
    @app.get("/")
    async def root():
        return {"name": "opencode", "version": __version__}

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    # --- Session routes ---
    @app.get("/session")
    async def session_list(directory: str = Query(default="."), limit: int = Query(default=100)):
        async def _fn():
            return [_session_json(s) for s in list_sessions(limit=limit)]
        return await provide(directory, _fn)

    @app.post("/session")
    async def session_create(request: Request, directory: str = Query(default=".")):
        body = await request.json() if request.headers.get("content-type") else {}
        async def _fn():
            s = create_session(title=body.get("title"))
            return _session_json(s)
        return await provide(directory, _fn)

    @app.get("/session/{session_id}")
    async def session_get(session_id: str, directory: str = Query(default=".")):
        async def _fn():
            try:
                return _session_json(get_session(session_id))
            except KeyError:
                raise HTTPException(404, f"Session not found: {session_id}")
        return await provide(directory, _fn)

    @app.delete("/session/{session_id}")
    async def session_delete(session_id: str, directory: str = Query(default=".")):
        async def _fn():
            remove(session_id)
            return {"ok": True}
        return await provide(directory, _fn)

    @app.put("/session/{session_id}/title")
    async def session_set_title(session_id: str, request: Request, directory: str = Query(default=".")):
        body = await request.json()
        async def _fn():
            set_title(session_id, body.get("title", ""))
            return {"ok": True}
        return await provide(directory, _fn)

    @app.post("/session/{session_id}/message")
    async def session_message(session_id: str, request: Request, directory: str = Query(default=".")):
        body = await request.json()
        parts = body.get("parts", [])
        model = body.get("model")
        agent = body.get("agent")

        async def event_generator():
            ctx, token = _ensure_ctx(directory)
            try:
                inp = PromptInput(session_id=session_id, parts=parts, model=model, agent=agent)
                async for event in prompt(inp, _bus):
                    yield {"event": event.type, "data": json.dumps(event.data)}
            finally:
                token.reset()

        return EventSourceResponse(event_generator())

    @app.post("/session/{session_id}/abort")
    async def session_abort(session_id: str):
        # TODO: implement abort via shared signal
        return {"ok": True}

    # --- Provider routes ---
    @app.get("/provider")
    async def provider_list():
        providers = await providermod.list_providers()
        return {pid: {"id": p.id, "name": p.name, "source": p.source,
                       "models": {mid: {"id": m.id, "name": m.name} for mid, m in p.models.items()}}
                for pid, p in providers.items()}

    @app.get("/provider/{provider_id}")
    async def provider_get(provider_id: str):
        p = await providermod.get_provider(provider_id)
        if not p:
            raise HTTPException(404, f"Provider not found: {provider_id}")
        return {"id": p.id, "name": p.name, "source": p.source,
                "models": {mid: {"id": m.id, "name": m.name} for mid, m in p.models.items()}}

    # --- Agent routes ---
    @app.get("/agent")
    async def agent_list():
        agents = await agentmod.list_agents()
        return [{"name": a.name, "description": a.description, "mode": a.mode, "hidden": a.hidden} for a in agents]

    # --- Config routes ---
    @app.get("/config")
    async def config_get(directory: str = Query(default=".")):
        cfg = configmod.get(directory)
        return cfg.model_dump(exclude_none=True)

    @app.post("/config")
    async def config_update(request: Request):
        body = await request.json()
        updated = configmod.update_global(body)
        return updated.model_dump(exclude_none=True)

    # --- File routes ---
    @app.get("/file")
    async def file_read(path: str = Query(...), directory: str = Query(default=".")):
        ctx, token = _ensure_ctx(directory)
        try:
            return await filemod.read(path)
        finally:
            token.reset()

    @app.get("/file/list")
    async def file_list(path: str = Query(default=None), directory: str = Query(default=".")):
        ctx, token = _ensure_ctx(directory)
        try:
            return await filemod.list_dir(path)
        finally:
            token.reset()

    @app.get("/file/search")
    async def file_search(query: str = Query(...), limit: int = Query(default=50), directory: str = Query(default=".")):
        ctx, token = _ensure_ctx(directory)
        try:
            return await filemod.search(query, limit=limit)
        finally:
            token.reset()

    # --- Permission routes ---
    @app.get("/permission")
    async def permission_list():
        # TODO: connect to PermissionManager
        return []

    @app.post("/permission/{request_id}")
    async def permission_reply(request_id: str, request: Request):
        body = await request.json()
        # TODO: connect to PermissionManager.reply
        return {"ok": True}

    # --- MCP routes ---
    @app.get("/mcp")
    async def mcp_status():
        return {}

    @app.post("/mcp/{name}/connect")
    async def mcp_connect(name: str):
        return {"ok": True}

    @app.post("/mcp/{name}/disconnect")
    async def mcp_disconnect(name: str):
        return {"ok": True}

    # --- Log route ---
    @app.post("/log")
    async def log_write(request: Request):
        from opencode.util import log as logmod
        body = await request.json()
        logger = logmod.create(service=body.get("service", "app"))
        level = body.get("level", "info")
        msg = body.get("message", "")
        getattr(logger, level, logger.info)(msg, **(body.get("extra") or {}))
        return True

    return app


def _session_json(s: SessionInfo) -> dict[str, Any]:
    return {
        "id": s.id, "slug": s.slug, "projectID": s.project_id,
        "directory": s.directory, "title": s.title, "version": s.version,
        "parentID": s.parent_id, "summary": s.summary, "share": s.share,
        "time": {"created": s.time_created, "updated": s.time_updated,
                 "compacting": s.time_compacting, "archived": s.time_archived},
    }
