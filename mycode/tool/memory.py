"""Memory tool — inspect and maintain versioned long-term memories."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from mycode.project.instance import current_or_none
from mycode.session.memory.memdir import MemoryType as LegacyMemoryType
from mycode.session.memory.memdir import sanitize_memory_name
from mycode.session.memory.service import MemoryRecord, MemoryService, MemoryServiceError
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult

MemoryAction = Literal[
    "list", "read", "write", "update", "delete", "inbox", "approve", "reject", "history", "export", "maintain",
]

_TYPE_MAP = {
    "user": "user_preference",
    "feedback": "feedback",
    "project": "project_fact",
    "reference": "reference",
}
_LEGACY_TYPE = {value: key for key, value in _TYPE_MAP.items()}


class MemoryParams(BaseModel):
    """Parameters for the memory tool."""

    action: MemoryAction = Field(description="Memory lifecycle operation.")
    memory_id: str | None = Field(default=None, description="Stable memory ID for lifecycle operations.")
    memory_ids: list[str] | None = Field(default=None, description="Candidate IDs for batch approve/reject.")
    filename: str | None = Field(default=None, description="Legacy filename alias for read/update/delete.")
    name: str | None = Field(default=None, description="Short memory subject for write/update.")
    description: str | None = Field(default=None, description="Trigger description used for future recall.")
    memory_type: LegacyMemoryType | None = Field(
        default=None, description="Compatibility type: user, feedback, project, or reference."
    )
    content: str | None = Field(default=None, description="Memory body for write/update.")
    scope_type: Literal["user", "project", "repository", "organization", "agent"] | None = None
    scope_id: str | None = None
    reason: str | None = None


class MemoryTool(CallableTool[MemoryParams]):
    id = "memory"
    description = (
        "List, recall, create, version, approve, reject, export, or delete long-term memory. "
        "Automatic discoveries stay pending in the inbox; explicit user requests may be written active. "
        "Only store durable information that cannot be derived from code, git, APIs, or project guidance."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return bool(args and args.get("action") in {"list", "read", "inbox", "history", "export"})

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return self.is_read_only(args)

    async def call(self, params: MemoryParams, ctx: ToolContext) -> ToolResult:
        inst = current_or_none()
        if not inst:
            return ToolError("No current project is available.", title="Memory")
        service = MemoryService(inst.worktree, project_id=inst.project.id, agent_id=ctx.agent)
        try:
            service.import_legacy_memdir()
            if params.action == "list":
                return _list_memories(service)
            if params.action == "inbox":
                return _list_inbox(service)
            if params.action == "read":
                return _read_memory(service, params)
            if params.action == "write":
                return _write_memory(service, params, ctx)
            if params.action == "update":
                return _update_memory(service, params)
            if params.action == "delete":
                return _delete_memory(service, params)
            if params.action == "approve":
                return _approve_memory(service, params)
            if params.action == "reject":
                return _reject_memory(service, params)
            if params.action == "history":
                return _history(service, params)
            if params.action == "export":
                return ToolOk(json.dumps(service.export(), ensure_ascii=False, indent=2), title="Memory export")
            if params.action == "maintain":
                expired = service.expire_due()
                consolidation = service.consolidate()
                projected = service.rebuild_projection()
                return ToolOk(
                    f"Memory maintenance complete: expired={expired}, projected={projected}, "
                    f"duplicates_rejected={consolidation['duplicates_rejected']}, "
                    f"conflict_groups={consolidation['conflict_groups']}",
                    title="Memory maintenance",
                )
        except MemoryServiceError as exc:
            return ToolError(str(exc), title=f"Memory {params.action}")
        return ToolError(f"Unsupported memory action: {params.action}", title="Memory")


def _legacy_alias(record: MemoryRecord) -> str:
    prefix = _LEGACY_TYPE.get(record.memory_type, "project")
    return f"{prefix}_{sanitize_memory_name(record.subject)}.md"


def _resolve(service: MemoryService, params: MemoryParams, *, status: str | None = None) -> MemoryRecord | None:
    identifier = params.memory_id or params.filename
    if not identifier:
        return None
    direct = service.get(identifier)
    if direct and (status is None or direct.status == status):
        return direct
    stem = identifier.removesuffix(".md")
    possible_id = stem.rsplit("_", 1)[-1]
    direct = service.get(possible_id)
    if direct and (status is None or direct.status == status):
        return direct
    records = service.list_memories(status=status or "active", limit=1000)
    if status is None:
        records.extend(service.list_memories(status=None, limit=1000))
    for record in records:
        if identifier in {record.subject, _legacy_alias(record), f"{_LEGACY_TYPE.get(record.memory_type, 'project')}_{record.id}.md"}:
            return record
    return None


def _list_memories(service: MemoryService) -> ToolResult:
    records = service.list_memories(status="active")
    if not records:
        return ToolOk("# Long-term memories\n\n(no active memories)", title="Memory list", metadata={"count": 0})
    lines = ["# Long-term memories (SQLite authority / MEMORY.md projection)", ""]
    for record in records:
        lines.append(
            f"- [{record.memory_type}] {record.subject} — {record.trigger_description or '(no trigger)'} "
            f"(id={record.id}, scope={record.scope_type}:{record.scope_id}, legacy_alias={_legacy_alias(record)})"
        )
    return ToolOk("\n".join(lines), title="Memory list", metadata={"count": len(records)})


def _list_inbox(service: MemoryService) -> ToolResult:
    records = service.list_memories(status="pending")
    if not records:
        return ToolOk("Memory inbox is empty.", title="Memory inbox", metadata={"count": 0})
    lines = ["# Memory inbox", ""]
    for record in records:
        lines.append(
            f"- {record.id}: [{record.memory_type}] {record.subject} "
            f"(source={record.source_kind}, confidence={record.confidence:.2f})"
        )
    return ToolOk("\n".join(lines), title="Memory inbox", metadata={"count": len(records)})


def _read_memory(service: MemoryService, params: MemoryParams) -> ToolResult:
    record = _resolve(service, params)
    if not record:
        return ToolError("memory_id or filename did not identify a memory.", title="Memory read")
    return ToolOk(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        title=f"Memory: {record.subject}",
        metadata={"memory_id": record.id, "status": record.status},
    )


def _write_memory(service: MemoryService, params: MemoryParams, ctx: ToolContext) -> ToolResult:
    missing = [
        field
        for field, value in {
            "name": params.name,
            "description": params.description,
            "memory_type": params.memory_type,
            "content": params.content,
        }.items()
        if not value
    ]
    if missing:
        return ToolError(f"Missing required fields for memory write: {', '.join(missing)}", title="Memory write")
    record = service.remember(
        subject=params.name or "",
        content=params.content or "",
        trigger_description=params.description or "",
        memory_type=_TYPE_MAP[params.memory_type or "project"],
        scope_type=params.scope_type or "project",
        scope_id=params.scope_id,
        source_session_id=ctx.session_id,
        source_message_ids=[ctx.message_id],
        created_by="user_explicit",
    )
    return ToolOk(
        f"Saved memory: {record.id} ({_legacy_alias(record)})",
        title="Memory write",
        metadata={"memory_id": record.id, "status": record.status},
    )


def _update_memory(service: MemoryService, params: MemoryParams) -> ToolResult:
    record = _resolve(service, params)
    if not record:
        return ToolError("Memory not found.", title="Memory update")
    if record.status == "pending":
        updated = service.edit_candidate(
            record.id,
            subject=params.name,
            content=params.content,
            trigger_description=params.description,
        )
        return ToolOk(
            f"Edited pending memory: {updated.id}",
            title="Memory update",
            metadata={"memory_id": updated.id, "status": updated.status},
        )
    updated = service.update(record.id, subject=params.name, content=params.content, trigger_description=params.description)
    return ToolOk(
        f"Updated memory: {updated.id} (supersedes {record.id})",
        title="Memory update",
        metadata={"memory_id": updated.id, "supersedes_id": record.id},
    )


def _delete_memory(service: MemoryService, params: MemoryParams) -> ToolResult:
    record = _resolve(service, params)
    if not record:
        return ToolError("Memory not found.", title="Memory delete")
    tombstone = service.delete(record.id, reason=params.reason or "")
    return ToolOk(
        f"Deleted memory: {params.filename or record.id}",
        title="Memory delete",
        metadata={"memory_id": record.id, "tombstone_id": tombstone.id},
    )


def _approve_memory(service: MemoryService, params: MemoryParams) -> ToolResult:
    if params.memory_ids:
        result = service.decide_batch(params.memory_ids, action="approve")
        return ToolOk(json.dumps(result, ensure_ascii=False), title="Memory batch approve", metadata=result)
    record = _resolve(service, params, status="pending")
    if not record:
        return ToolError("Pending memory not found.", title="Memory approve")
    approved = service.approve(record.id)
    return ToolOk(f"Approved memory: {approved.id}", title="Memory approve", metadata={"memory_id": approved.id})


def _reject_memory(service: MemoryService, params: MemoryParams) -> ToolResult:
    if params.memory_ids:
        result = service.decide_batch(params.memory_ids, action="reject", reason=params.reason or "")
        return ToolOk(json.dumps(result, ensure_ascii=False), title="Memory batch reject", metadata=result)
    record = _resolve(service, params, status="pending")
    if not record:
        return ToolError("Pending memory not found.", title="Memory reject")
    rejected = service.reject(record.id, reason=params.reason or "")
    return ToolOk(f"Rejected memory: {rejected.id}", title="Memory reject", metadata={"memory_id": rejected.id})


def _history(service: MemoryService, params: MemoryParams) -> ToolResult:
    record = _resolve(service, params)
    if not record:
        return ToolError("Memory not found.", title="Memory history")
    history = [item.to_dict() for item in service.history(record.id)]
    return ToolOk(json.dumps(history, ensure_ascii=False, indent=2), title="Memory history", metadata={"count": len(history)})


tool = MemoryTool()
