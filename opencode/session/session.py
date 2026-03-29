"""Session CRUD operations. Equivalent to src/session/index.ts."""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any
from opencode.project.instance import current
from opencode.util import ids, slug as slugmod

@dataclass
class SessionInfo:
    id: str
    slug: str
    project_id: str
    directory: str
    title: str
    version: str = "0.1.0"
    parent_id: str | None = None
    workspace_id: str | None = None
    summary: dict[str, Any] | None = None
    share: dict[str, str] | None = None
    revert: dict[str, Any] | None = None
    permission: list[dict[str, Any]] | None = None
    time_created: int = 0
    time_updated: int = 0
    time_compacting: int | None = None
    time_archived: int | None = None

class BusyError(Exception):
    def __init__(self, session_id: str):
        self.session_id = session_id
        super().__init__(f"Session {session_id} is busy")

# In-memory store (will be backed by SQLite in full implementation)
_sessions: dict[str, SessionInfo] = {}

def create(*, parent_id: str | None = None, title: str | None = None) -> SessionInfo:
    ctx = current()
    now = int(time.time() * 1000)
    session = SessionInfo(
        id=ids.session_id(),
        slug=slugmod.create(),
        project_id=ctx.project.id,
        directory=ctx.directory,
        title=title or f"New session - {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        parent_id=parent_id,
        time_created=now,
        time_updated=now,
    )
    _sessions[session.id] = session
    return session

def get(session_id: str) -> SessionInfo:
    s = _sessions.get(session_id)
    if not s:
        raise KeyError(f"Session not found: {session_id}")
    return s

def list_sessions() -> list[SessionInfo]:
    return sorted(_sessions.values(), key=lambda s: s.time_updated, reverse=True)

def touch(session_id: str) -> None:
    s = get(session_id)
    s.time_updated = int(time.time() * 1000)

def remove(session_id: str) -> None:
    _sessions.pop(session_id, None)

def set_title(session_id: str, title: str) -> None:
    get(session_id).title = title
