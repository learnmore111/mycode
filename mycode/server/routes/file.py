"""File API routes."""
from __future__ import annotations

import base64
import hashlib
import os
import platform
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from mycode.file import file as filemod
from mycode.project.instance import InstanceContext, ProjectInfo, set_context

router = APIRouter(prefix="/file", tags=["file"])


@router.get("/system-paths")
async def system_paths() -> Any:
    """Return quick-access system paths (home, desktop, documents, downloads)."""
    home = Path.home()
    info: dict[str, Any] = {
        "home": str(home),
        "desktop": str(home / "Desktop"),
        "documents": str(home / "Documents"),
        "downloads": str(home / "Downloads"),
    }
    if platform.system() == "Darwin" or platform.system() == "Windows":
        info["desktop"] = str(home / "Desktop")
        info["documents"] = str(home / "Documents")
        info["downloads"] = str(home / "Downloads")
    else:
        info["desktop"] = str(home)
        info["documents"] = str(home)
        info["downloads"] = str(home / "Downloads")
    # Only include paths that exist
    return {k: v for k, v in info.items() if Path(v).exists()}


@router.get("/browse")
async def browse_directory(path: str = Query(default=".")) -> Any:
    """Browse any local directory, not just the project directory."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise HTTPException(404, f"Path does not exist: {path}")
    if not target.is_dir():
        raise HTTPException(400, f"Path is not a directory: {path}")

    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            entries.append({
                "name": child.name,
                "type": "directory" if child.is_dir() else "file",
                "path": str(child),
                "size": child.stat().st_size if child.is_file() else None,
            })
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {path}")

    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "entries": entries,
    }


def _ensure_ctx(directory: str) -> Any:
    project = ProjectInfo(id="global", worktree=directory)
    ctx = InstanceContext(directory=directory, worktree=directory, project=project)
    return ctx, set_context(ctx)


@router.get("")
async def file_read(path: str = Query(...), directory: str = Query(default=".")) -> Any:
    _ctx, token = _ensure_ctx(directory)
    try:
        return await filemod.read(path)
    finally:
        token.reset()


@router.get("/list")
async def file_list(path: str = Query(default=None), directory: str = Query(default=".")) -> Any:
    _ctx, token = _ensure_ctx(directory)
    try:
        return await filemod.list_dir(path)
    finally:
        token.reset()


@router.get("/search")
async def file_search(query: str = Query(...), limit: int = Query(default=50), directory: str = Query(default=".")) -> Any:
    _ctx, token = _ensure_ctx(directory)
    try:
        return await filemod.search(query, limit=limit)
    finally:
        token.reset()


# --- Attachments ----------------------------------------------------------

# Whitelist of MIME prefixes we accept. Keeps the agent away from
# unsupported binaries (model-provider defaults are image-only anyway).
_ALLOWED_PREFIXES = ("image/", "application/pdf", "audio/")
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/attachment")
async def upload_attachment(
    request: Request,
    directory: str = Query(default="."),
    session_id: str = Query(default="_shared"),
) -> Any:
    """Persist an attachment and return a file:// URL the frontend can
    pass back via ``message.parts[i].content`` as an image/pdf/audio part.

    Body must be JSON of the form::

        {"mime": "image/png", "data": "<base64 payload>"}

    The file lands under ``<directory>/.mycode/attachments/<session_id>/``
    with a content-addressable name. We deliberately do NOT write to a
    temp dir — this keeps the attachment auditable in the shadow-git
    snapshot for the session it belongs to.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    mime = body.get("mime")
    data = body.get("data")
    if not isinstance(mime, str) or not any(mime.startswith(p) for p in _ALLOWED_PREFIXES):
        raise HTTPException(400, f"Unsupported mime type: {mime!r}")
    if not isinstance(data, str) or not data:
        raise HTTPException(400, "`data` must be a non-empty base64 string")

    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        raise HTTPException(400, f"Invalid base64: {exc}") from exc
    if len(raw) > _MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, f"Attachment too large (> {_MAX_ATTACHMENT_BYTES} bytes)")

    # Content-addressed filename so identical uploads dedupe.
    digest = hashlib.sha256(raw).hexdigest()[:20]
    ext = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
        "image/gif": ".gif", "application/pdf": ".pdf",
    }.get(mime, "")
    base_dir = Path(directory) / ".mycode" / "attachments" / session_id
    base_dir.mkdir(parents=True, exist_ok=True)
    file_path = base_dir / f"{digest}{ext}"
    if not file_path.exists():
        file_path.write_bytes(raw)

    return {
        "url": f"file://{os.path.abspath(file_path)}",
        "path": str(file_path),
        "mime": mime,
        "size": len(raw),
        "sha256": digest,
        "created_at": int(time.time() * 1000),
    }
