from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from mycode.storage.database import session_scope
from mycode.storage.models import OrchestrationRunTable

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _preview(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


@dataclass
class OrchestrationRunInfo:
    run_id: str
    flow: str
    mode: str
    directory: str | None
    task_text: str | None
    vars: dict[str, str] = field(default_factory=dict)
    max_turns: int = 0
    walltime_seconds: float = 0.0
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None

    def is_done(self) -> bool:
        return self.status in _TERMINAL_RUN_STATUSES

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "flow": self.flow,
            "mode": self.mode,
            "status": self.status,
            "done": self.is_done(),
            "cancelled": self.status == "cancelled",
            "cancel_requested": self.cancel_requested,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "has_result": self.result is not None,
            "error": self.error,
        }

    def to_detail(self) -> dict[str, Any]:
        payload = self.to_summary()
        payload.update({
            "directory": self.directory,
            "task_preview": _preview(self.task_text or ""),
            "vars": dict(self.vars),
            "max_turns": self.max_turns,
            "walltime_seconds": self.walltime_seconds,
            "result": self.result,
        })
        return payload


def _from_row(row: OrchestrationRunTable) -> OrchestrationRunInfo:
    return OrchestrationRunInfo(
        run_id=row.run_id,
        flow=row.flow,
        mode=row.mode,
        directory=row.directory,
        task_text=row.task_text,
        vars=dict(row.vars),
        max_turns=row.max_turns,
        walltime_seconds=row.walltime_seconds,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cancel_requested=row.cancel_requested,
        error=row.error,
        result=row.result,
    )


def _apply_row(row: OrchestrationRunTable, record: OrchestrationRunInfo) -> None:
    row.flow = record.flow
    row.mode = record.mode
    row.directory = record.directory
    row.task_text = record.task_text
    row.vars = dict(record.vars)
    row.max_turns = record.max_turns
    row.walltime_seconds = record.walltime_seconds
    row.status = record.status
    row.started_at = record.started_at
    row.finished_at = record.finished_at
    row.cancel_requested = record.cancel_requested
    row.error = record.error
    row.result = record.result


def save_run_record(record: OrchestrationRunInfo) -> OrchestrationRunInfo:
    with session_scope() as db:
        row = db.query(OrchestrationRunTable).filter(OrchestrationRunTable.run_id == record.run_id).first()
        if row is None:
            row = OrchestrationRunTable(run_id=record.run_id)
            db.add(row)
        _apply_row(row, record)
        db.commit()
        return _from_row(row)


def get_run_record(run_id: str) -> OrchestrationRunInfo | None:
    with session_scope() as db:
        row = db.query(OrchestrationRunTable).filter(OrchestrationRunTable.run_id == run_id).first()
        return _from_row(row) if row is not None else None


def delete_run_record(run_id: str) -> bool:
    with session_scope() as db:
        row = db.query(OrchestrationRunTable).filter(OrchestrationRunTable.run_id == run_id).first()
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True


def list_run_records(*, limit: int | None = None) -> list[OrchestrationRunInfo]:
    with session_scope() as db:
        query = db.query(OrchestrationRunTable).order_by(OrchestrationRunTable.started_at.desc())
        if limit is not None:
            query = query.limit(limit)
        return [_from_row(row) for row in query.all()]


__all__ = ["OrchestrationRunInfo", "delete_run_record", "get_run_record", "list_run_records", "save_run_record"]
