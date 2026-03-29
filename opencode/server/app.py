"""FastAPI application — complete API. Equivalent to src/server/server.ts + routes/."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from opencode import __version__
from opencode.bus.bus import Bus
from opencode.server.routes import config, event, file, mcp, permission, project, provider, session


def create_app() -> FastAPI:
    app = FastAPI(title="opencode", version=__version__, description="OpenCode AI coding agent API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # Shared bus for SSE events
    bus = Bus()
    event.set_bus(bus)

    # --- Health ---
    @app.get("/")
    async def root():
        return {"name": "opencode", "version": __version__}

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    # --- Agent route (small, kept inline) ---
    @app.get("/agent")
    async def agent_list():
        from opencode.agent import agent as agentmod
        agents = await agentmod.list_agents()
        return [{"name": a.name, "description": a.description, "mode": a.mode, "hidden": a.hidden} for a in agents]

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

    # --- Include route modules ---
    app.include_router(session.router)
    app.include_router(provider.router)
    app.include_router(config.router)
    app.include_router(file.router)
    app.include_router(permission.router)
    app.include_router(mcp.router)
    app.include_router(event.router)
    app.include_router(project.router)

    return app
