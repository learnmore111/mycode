"""Session CRUD with SQLite persistence. Equivalent to src/session/index.ts."""
from __future__ import annotations
import json, time
from dataclasses import dataclass, field
from typing import Any
from opencode.project.instance import current, current_or_none
from opencode.storage.database import get_session as get_db_session
from opencode.storage.models import SessionTable
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


def _from_row(row: SessionTable) -> SessionInfo:
    summary = None
    if row.summary_additions is not None or row.summary_deletions is not None:
        summary = {
            "additions": row.summary_additions or 0,
            "deletions": row.summary_deletions or 0,
            "files": row.summary_files or 0,
        }
        if row.summary_diffs:
            try:
                summary["diffs"] = json.loads(row.summary_diffs)
            except (json.JSONDecodeError, TypeError):
                pass
    return SessionInfo(
        id=row.id, slug=row.slug, project_id=row.project_id, directory=row.directory,
        title=row.title, version=row.version, parent_id=row.parent_id,
        workspace_id=row.workspace_id,
        summary=summary,
        share={"url": row.share_url} if row.share_url else None,
        revert=json.loads(row.revert) if row.revert else None,
        permission=json.loads(row.permission) if row.permission else None,
        time_created=row.time_created, time_updated=row.time_updated,
        time_compacting=row.time_compacting, time_archived=row.time_archived,
    )


def _to_row_dict(info: SessionInfo) -> dict[str, Any]:
    return {
        "id": info.id, "slug": info.slug, "project_id": info.project_id,
        "directory": info.directory, "title": info.title, "version": info.version,
        "parent_id": info.parent_id, "workspace_id": info.workspace_id,
        "share_url": info.share["url"] if info.share else None,
        "revert": json.dumps(info.revert) if info.revert else None,
        "permission": json.dumps(info.permission) if info.permission else None,
        "summary_additions": info.summary["additions"] if info.summary else None,
        "summary_deletions": info.summary["deletions"] if info.summary else None,
        "summary_files": info.summary.get("files") if info.summary else None,
        "summary_diffs": json.dumps(info.summary.get("diffs")) if info.summary and info.summary.get("diffs") else None,
        "time_created": info.time_created, "time_updated": info.time_updated,
        "time_compacting": info.time_compacting, "time_archived": info.time_archived,
    }


def create(*, parent_id: str | None = None, title: str | None = None) -> SessionInfo:
    ctx = current()
    now = int(time.time() * 1000)
    info = SessionInfo(
        id=ids.session_id(), slug=slugmod.create(), project_id=ctx.project.id,
        directory=ctx.directory,
        title=title or f"New session - {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        parent_id=parent_id, time_created=now, time_updated=now,
    )
    db = get_db_session()
    try:
        db.add(SessionTable(**_to_row_dict(info)))
        db.commit()
    finally:
        db.close()
    return info


def get(session_id: str) -> SessionInfo:
    db = get_db_session()
    try:
        row = db.query(SessionTable).filter(SessionTable.id == session_id).first()
        if not row:
            raise KeyError(f"Session not found: {session_id}")
        return _from_row(row)
    finally:
        db.close()


def list_sessions(*, project_id: str | None = None, limit: int = 100) -> list[SessionInfo]:
    ctx = current_or_none()
    pid = project_id or (ctx.project.id if ctx else None)
    db = get_db_session()
    try:
        q = db.query(SessionTable)
        if pid:
            q = q.filter(SessionTable.project_id == pid)
        rows = q.order_by(SessionTable.time_updated.desc()).limit(limit).all()
        return [_from_row(r) for r in rows]
    finally:
        db.close()


def touch(session_id: str) -> None:
    db = get_db_session()
    try:
        row = db.query(SessionTable).filter(SessionTable.id == session_id).first()
        if row:
            row.time_updated = int(time.time() * 1000)
            db.commit()
    finally:
        db.close()


def remove(session_id: str) -> None:
    db = get_db_session()
    try:
        db.query(SessionTable).filter(SessionTable.id == session_id).delete()
        db.commit()
    finally:
        db.close()


def set_title(session_id: str, title: str) -> None:
    db = get_db_session()
    try:
        row = db.query(SessionTable).filter(SessionTable.id == session_id).first()
        if row:
            row.title = title
            row.time_updated = int(time.time() * 1000)
            db.commit()
    finally:
        db.close()


def set_summary(session_id: str, summary: dict[str, Any]) -> None:
    db = get_db_session()
    try:
        row = db.query(SessionTable).filter(SessionTable.id == session_id).first()
        if row:
            row.summary_additions = summary.get("additions")
            row.summary_deletions = summary.get("deletions")
            row.summary_files = summary.get("files")
            row.time_updated = int(time.time() * 1000)
            db.commit()
    finally:
        db.close()
