from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mycode import __version__
from mycode.bus.bus import Bus
from mycode.server.routes import config, event, file, git, mcp, permission, project, provider, session

# Maximum sizes for log endpoint to prevent DoS
_MAX_LOG_SERVICE_LEN = 128
_MAX_LOG_MESSAGE_LEN = 10_000
_ALLOWED_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})


def create_app() -> FastAPI:
    app = FastAPI(title="mycode", version=__version__, description="MyCode AI coding agent API")

    # CORS: restrict origins via env var, default to localhost only
    allowed_origins = os.environ.get("OPENCODE_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in allowed_origins if o.strip()],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    # Shared bus for SSE events
    bus = Bus()
    event.set_bus(bus)

    # Shared permission manager — wired to both API routes and prompt engine
    from mycode.permission.permission import PermissionManager
    perm_manager = PermissionManager(bus, project_id="global")
    permission.set_manager(perm_manager)
    # Store on app.state so session route can access it
    app.state.permission_manager = perm_manager

    # --- Health ---
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/info")
    async def api_info():
        return {"name": "mycode", "version": __version__}

    # --- Agent route (small, kept inline) ---
    @app.get("/agent")
    async def agent_list():
        from mycode.agent import agent as agentmod
        agents = await agentmod.list_agents()
        return [{"name": a.name, "description": a.description, "mode": a.mode, "hidden": a.hidden} for a in agents]

    # --- Log route (with input validation) ---
    @app.post("/log")
    async def log_write(request: Request):
        from mycode.util import log as logmod
        body = await request.json()
        service = str(body.get("service", "app"))[:_MAX_LOG_SERVICE_LEN]
        level = str(body.get("level", "info")).lower()
        msg = str(body.get("message", ""))[:_MAX_LOG_MESSAGE_LEN]

        if level not in _ALLOWED_LOG_LEVELS:
            level = "info"

        logger = logmod.create(service=service)
        extra = body.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {}
        getattr(logger, level, logger.info)(msg, **extra)
        return True

    # --- Include route modules ---
    app.include_router(session.router)
    app.include_router(provider.router)
    app.include_router(config.router)
    app.include_router(file.router)
    app.include_router(git.router)
    app.include_router(permission.router)
    app.include_router(mcp.router)
    app.include_router(event.router)
    app.include_router(project.router)

    # --- Static files: serve Web UI from web/dist if it exists ---
    # Try multiple locations: next to the mycode package, or in the project root
    _web_dist = None
    for candidate in [
        Path(__file__).resolve().parent.parent.parent / "web" / "dist",  # repo root / web / dist
    ]:
        if (candidate / "index.html").is_file():
            _web_dist = candidate
            break

    if _web_dist:
        # Mount assets directory for JS/CSS/images
        assets_dir = _web_dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static-assets")

        # SPA catch-all: return index.html for any non-API path
        _index_html = _web_dist / "index.html"

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            # Serve actual files from dist if they exist (e.g. favicon, manifest)
            file_path = _web_dist / path  # type: ignore[operator]
            if file_path.is_file() and ".." not in path:
                return FileResponse(str(file_path))
            return FileResponse(str(_index_html))

    return app
