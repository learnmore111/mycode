"""FastAPI application. Equivalent to src/server/server.ts."""
from __future__ import annotations
import json
from typing import Any
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from opencode import __version__
from opencode.bus.bus import Bus
from opencode.session.prompt import PromptInput, prompt
from opencode.session.session import (
    create as create_session, get as get_session, list_sessions, remove, set_title,
)
from opencode.provider import provider as providermod
from opencode.agent import agent as agentmod
from opencode.project.instance import InstanceContext, ProjectInfo, provide

_bus = Bus()

def create_app() -> FastAPI:
    app = FastAPI(title="opencode", version=__version__, description="OpenCode AI coding agent API")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/")
    async def root():
        return {"name": "opencode", "version": __version__}

    # --- Session routes ---
    @app.get("/session")
    async def session_list(directory: str = Query(default=".")):
        async def _fn():
            return [{"id": s.id, "title": s.title, "time_updated": s.time_updated} for s in list_sessions()]
        return await provide(directory, _fn)

    @app.post("/session")
    async def session_create(directory: str = Query(default="."), title: str | None = None):
        async def _fn():
            s = create_session(title=title)
            return {"id": s.id, "slug": s.slug, "title": s.title}
        return await provide(directory, _fn)

    @app.delete("/session/{session_id}")
    async def session_delete(session_id: str, directory: str = Query(default=".")):
        async def _fn():
            remove(session_id)
            return {"ok": True}
        return await provide(directory, _fn)

    @app.post("/session/{session_id}/message")
    async def session_message(session_id: str, request: Request, directory: str = Query(default=".")):
        body = await request.json()
        parts = body.get("parts", [])
        model = body.get("model")
        agent = body.get("agent")

        async def event_generator():
            async def _inner():
                inp = PromptInput(
                    session_id=session_id, parts=parts, model=model, agent=agent,
                )
                async for event in prompt(inp, _bus):
                    yield {"event": event.type, "data": json.dumps(event.data)}
            # Run within instance context
            project = ProjectInfo(id="global", worktree=directory)
            ctx = InstanceContext(directory=directory, worktree=directory, project=project)
            from opencode.project.instance import set_context
            token = set_context(ctx)
            try:
                async for item in _inner():
                    yield item
            finally:
                token.reset()

        return EventSourceResponse(event_generator())

    # --- Provider routes ---
    @app.get("/provider")
    async def provider_list():
        providers = await providermod.list_providers()
        return {pid: {"id": p.id, "name": p.name, "source": p.source, "models": list(p.models.keys())}
                for pid, p in providers.items()}

    # --- Agent routes ---
    @app.get("/agent")
    async def agent_list():
        agents = await agentmod.list_agents()
        return [{"name": a.name, "description": a.description, "mode": a.mode} for a in agents]

    # --- Config/health ---
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    return app
