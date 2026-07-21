"""Versioned long-term memory and inbox API routes."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from mycode.project.project import from_directory
from mycode.session.memory.service import MemoryRejectedError, MemoryService, MemoryServiceError

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryCreateBody(BaseModel):
    subject: str
    content: str
    trigger_description: str = ""
    memory_type: Literal[
        "user_preference", "feedback", "project_fact", "episodic_experience", "reference", "procedure_candidate"
    ] = "project_fact"
    scope_type: Literal["user", "project", "repository", "organization", "agent"] = "project"
    scope_id: str | None = None
    source_session_id: str | None = None
    source_message_ids: list[str] = []
    source_kind: Literal[
        "user_statement", "code_evidence", "git_evidence", "tool_output", "external_content", "agent_inference"
    ] = "user_statement"
    evidence_refs: list[Any] = []
    confidence: float = 1.0
    expires_at: int | None = None
    pending: bool = False


class MemoryUpdateBody(BaseModel):
    subject: str | None = None
    content: str | None = None
    trigger_description: str | None = None


class MemoryDecisionBody(BaseModel):
    reason: str = ""


class MemoryBatchBody(BaseModel):
    memory_ids: list[str]
    action: Literal["approve", "reject"]
    reason: str = ""


class MemoryScopeDeleteBody(BaseModel):
    scope_type: Literal["user", "project", "repository", "organization", "agent"]
    scope_id: str | None = None
    reason: str = "scope deletion"


async def _service(directory: str) -> MemoryService:
    project = await from_directory(directory)
    return MemoryService(project.worktree, project_id=project.id)


@router.get("")
async def memory_list(
    directory: str = Query(default="."),
    status: str | None = Query(default="active"),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    service = await _service(directory)
    service.import_legacy_memdir()
    return [
        record.to_dict()
        for record in service.list_memories(status=status, scope_type=scope_type, scope_id=scope_id)
    ]


@router.get("/inbox")
async def memory_inbox(directory: str = Query(default=".")) -> list[dict[str, Any]]:
    service = await _service(directory)
    return [record.to_dict() for record in service.list_memories(status="pending")]


@router.post("/inbox/batch")
async def memory_inbox_batch(
    body: MemoryBatchBody, directory: str = Query(default=".")
) -> dict[str, Any]:
    return (await _service(directory)).decide_batch(
        body.memory_ids, action=body.action, reason=body.reason, actor="api"
    )


@router.get("/export")
async def memory_export(
    directory: str = Query(default="."), include_deleted: bool = Query(default=False)
) -> dict[str, Any]:
    return (await _service(directory)).export(include_deleted=include_deleted)


@router.post("/maintenance")
async def memory_maintenance(directory: str = Query(default=".")) -> dict[str, int]:
    service = await _service(directory)
    result = service.consolidate()
    return {"expired": service.expire_due(), "projected": service.rebuild_projection(), **result}


@router.delete("/scope")
async def memory_delete_scope(
    body: MemoryScopeDeleteBody, directory: str = Query(default=".")
) -> dict[str, int]:
    try:
        count = (await _service(directory)).delete_scope(
            body.scope_type, body.scope_id, actor="api", reason=body.reason
        )
    except MemoryServiceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"deleted_roots": count}


@router.post("")
async def memory_create(body: MemoryCreateBody, directory: str = Query(default=".")) -> dict[str, Any]:
    service = await _service(directory)
    kwargs = body.model_dump(exclude={"pending"})
    try:
        if body.pending:
            record = service.create(**kwargs, status="pending", created_by="api")
        else:
            record = service.remember(**kwargs, created_by="api")
    except (MemoryRejectedError, MemoryServiceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return record.to_dict()


@router.get("/{memory_id}")
async def memory_get(memory_id: str, directory: str = Query(default=".")) -> dict[str, Any]:
    record = (await _service(directory)).get(memory_id)
    if not record:
        raise HTTPException(status_code=404, detail="Memory not found")
    return record.to_dict()


@router.get("/{memory_id}/history")
async def memory_history(memory_id: str, directory: str = Query(default=".")) -> list[dict[str, Any]]:
    service = await _service(directory)
    records = service.history(memory_id)
    if not records:
        raise HTTPException(status_code=404, detail="Memory not found")
    return [record.to_dict() for record in records]


@router.get("/{memory_id}/audit")
async def memory_audit(memory_id: str, directory: str = Query(default=".")) -> list[dict[str, Any]]:
    service = await _service(directory)
    if not service.get(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return service.audit_history(memory_id)


@router.patch("/{memory_id}")
async def memory_update(
    memory_id: str, body: MemoryUpdateBody, directory: str = Query(default=".")
) -> dict[str, Any]:
    try:
        service = await _service(directory)
        current = service.get(memory_id)
        if not current:
            raise MemoryServiceError("Memory not found")
        if current.status == "pending":
            record = service.edit_candidate(memory_id, **body.model_dump())
        else:
            record = service.update(memory_id, **body.model_dump())
    except MemoryServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.to_dict()


@router.post("/{memory_id}/approve")
async def memory_approve(memory_id: str, directory: str = Query(default=".")) -> dict[str, Any]:
    try:
        return (await _service(directory)).approve(memory_id).to_dict()
    except MemoryServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{memory_id}/reject")
async def memory_reject(
    memory_id: str, body: MemoryDecisionBody, directory: str = Query(default=".")
) -> dict[str, Any]:
    try:
        return (await _service(directory)).reject(memory_id, reason=body.reason).to_dict()
    except MemoryServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{memory_id}")
async def memory_delete(
    memory_id: str, body: MemoryDecisionBody | None = None, directory: str = Query(default=".")
) -> dict[str, Any]:
    try:
        tombstone = (await _service(directory)).delete(memory_id, reason=body.reason if body else "")
    except MemoryServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return tombstone.to_dict()
