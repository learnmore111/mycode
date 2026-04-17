from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any

from opencode.project.instance import current, current_or_none
from opencode.storage.database import get_session as get_db_session
from opencode.storage.models import SessionPauseTable, SessionTable
from opencode.util import ids
from opencode.util import slug as slugmod


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
    visible: bool = True


@dataclass
class PausedRunInfo:
    session_id: str
    last_user_text: str
    partial_text: str | None = None
    paused_at: int = 0
    model: str | None = None
    agent: str | None = None
    time_updated: int = 0


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
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                summary["diffs"] = json.loads(row.summary_diffs)
    return SessionInfo(
        id=row.id,
        slug=row.slug,
        project_id=row.project_id,
        directory=row.directory,
        title=row.title,
        version=row.version,
        parent_id=row.parent_id,
        workspace_id=row.workspace_id,
        summary=summary,
        share={"url": row.share_url} if row.share_url else None,
        revert=json.loads(row.revert) if row.revert else None,
        permission=json.loads(row.permission) if row.permission else None,
        time_created=row.time_created,
        time_updated=row.time_updated,
        time_compacting=row.time_compacting,
        time_archived=row.time_archived,
        visible=bool(row.visible),
    )


def _to_row_dict(info: SessionInfo) -> dict[str, Any]:
    return {
        "id": info.id,
        "slug": info.slug,
        "project_id": info.project_id,
        "directory": info.directory,
        "title": info.title,
        "version": info.version,
        "parent_id": info.parent_id,
        "workspace_id": info.workspace_id,
        "share_url": info.share["url"] if info.share else None,
        "revert": json.dumps(info.revert) if info.revert else None,
        "permission": json.dumps(info.permission) if info.permission else None,
        "summary_additions": info.summary["additions"] if info.summary else None,
        "summary_deletions": info.summary["deletions"] if info.summary else None,
        "summary_files": info.summary.get("files") if info.summary else None,
        "summary_diffs": json.dumps(info.summary.get("diffs")) if info.summary and info.summary.get("diffs") else None,
        "time_created": info.time_created,
        "time_updated": info.time_updated,
        "time_compacting": info.time_compacting,
        "time_archived": info.time_archived,
    }


def _paused_run_from_row(row: SessionPauseTable) -> PausedRunInfo:
    return PausedRunInfo(
        session_id=row.session_id,
        last_user_text=row.last_user_text,
        partial_text=row.partial_text,
        paused_at=row.time_paused,
        model=row.model,
        agent=row.agent,
        time_updated=row.time_updated,
    )


def create(*, parent_id: str | None = None, title: str | None = None) -> SessionInfo:
    ctx = current()
    now = int(time.time() * 1000)
    info = SessionInfo(
        id=ids.session_id(),
        slug=slugmod.create(),
        project_id=ctx.project.id,
        directory=ctx.directory,
        title=title or f"New session - {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        parent_id=parent_id,
        time_created=now,
        time_updated=now,
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
        q = db.query(SessionTable).filter(SessionTable.visible == 1)
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
    """Soft-delete a session (set visible = 0)."""
    db = get_db_session()
    try:
        row = db.query(SessionTable).filter(SessionTable.id == session_id).first()
        if row:
            row.visible = 0
            row.time_updated = int(time.time() * 1000)
            db.commit()
    finally:
        db.close()

    clear_paused_run(session_id)

    # Clean up per-session in-memory state
    try:
        from opencode.tool.todo import clear_todos

        clear_todos(session_id)
    except Exception:
        pass


def restore(session_id: str) -> None:
    """Restore a soft-deleted session (set visible = 1)."""
    db = get_db_session()
    try:
        row = db.query(SessionTable).filter(SessionTable.id == session_id).first()
        if not row:
            raise KeyError(f"Session not found: {session_id}")
        row.visible = 1
        row.time_updated = int(time.time() * 1000)
        db.commit()
    finally:
        db.close()


def list_deleted(*, project_id: str | None = None, limit: int = 100) -> list[SessionInfo]:
    """List soft-deleted sessions."""
    ctx = current_or_none()
    pid = project_id or (ctx.project.id if ctx else None)
    db = get_db_session()
    try:
        q = db.query(SessionTable).filter(SessionTable.visible == 0)
        if pid:
            q = q.filter(SessionTable.project_id == pid)
        rows = q.order_by(SessionTable.time_updated.desc()).limit(limit).all()
        return [_from_row(r) for r in rows]
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
            row.summary_diffs = json.dumps(summary.get("diffs")) if summary.get("diffs") else None
            row.time_updated = int(time.time() * 1000)
            db.commit()
    finally:
        db.close()


def get_paused_run(session_id: str) -> PausedRunInfo | None:
    db = get_db_session()
    try:
        row = db.query(SessionPauseTable).filter(SessionPauseTable.session_id == session_id).first()
        return _paused_run_from_row(row) if row else None
    finally:
        db.close()


def set_paused_run(
    session_id: str,
    *,
    last_user_text: str,
    partial_text: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    paused_at: int | None = None,
) -> PausedRunInfo:
    now = int(time.time() * 1000)
    paused_at = paused_at or now
    db = get_db_session()
    try:
        row = db.query(SessionPauseTable).filter(SessionPauseTable.session_id == session_id).first()
        if not row:
            row = SessionPauseTable(session_id=session_id)
            db.add(row)

        row.last_user_text = last_user_text
        row.partial_text = partial_text
        row.model = model
        row.agent = agent
        row.time_paused = paused_at
        row.time_updated = now
        db.commit()
        return _paused_run_from_row(row)
    finally:
        db.close()


def clear_paused_run(session_id: str) -> None:
    db = get_db_session()
    try:
        row = db.query(SessionPauseTable).filter(SessionPauseTable.session_id == session_id).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()
