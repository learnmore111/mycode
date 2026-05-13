"""Memory tool — inspect and maintain structured long-term memories."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from mycode.project.instance import current_or_none
from mycode.session.memory.memdir import (
    MemoryType,
    delete_memory,
    format_memory_manifest,
    load_memory_index,
    save_memory,
    scan_memory_files,
    update_memory,
)
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult

MemoryAction = Literal["list", "read", "write", "update", "delete"]


class MemoryParams(BaseModel):
    """Parameters for the memory tool."""

    action: MemoryAction = Field(description="Operation to perform: list, read, write, update, or delete.")
    filename: str | None = Field(default=None, description="Memory filename for read/update/delete, e.g. feedback_terse.md.")
    name: str | None = Field(default=None, description="Short memory name for write/update.")
    description: str | None = Field(default=None, description="One-line description used for future recall.")
    memory_type: MemoryType | None = Field(default=None, description="Memory type: user, feedback, project, or reference.")
    content: str | None = Field(default=None, description="Markdown memory body for write/update.")


class MemoryTool(CallableTool[MemoryParams]):
    id = "memory"
    description = (
        "List, read, write, update, or delete structured long-term memories. "
        "Use list to inspect the MEMORY.md index and available memory files. "
        "Use read to load a specific memory when the index suggests it may be relevant. "
        "Use write/update only for durable information that cannot be derived from the codebase, git history, or CLAUDE.md."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        if not args:
            return False
        return args.get("action") in {"list", "read"}

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return self.is_read_only(args)

    async def call(self, params: MemoryParams, ctx: ToolContext) -> ToolResult:
        inst = current_or_none()
        if not inst:
            return ToolError("No current project is available.", title="Memory")
        project_path = inst.directory

        if params.action == "list":
            return _list_memories(project_path)
        if params.action == "read":
            return _read_memory(project_path, params.filename)
        if params.action == "write":
            return _write_memory(project_path, params)
        if params.action == "update":
            return _update_memory(project_path, params)
        if params.action == "delete":
            return _delete_memory(project_path, params.filename)

        return ToolError(f"Unsupported memory action: {params.action}", title="Memory")


def _list_memories(project_path: str) -> ToolResult:
    index = load_memory_index(project_path)
    entries = scan_memory_files(project_path)
    manifest = format_memory_manifest(entries) or "(no memory files)"
    text = (
        "# MEMORY.md\n"
        f"{index or '(no MEMORY.md index)'}\n\n"
        "# Available memory files\n"
        f"{manifest}"
    )
    return ToolOk(text, title="Memory list", metadata={"count": len(entries)})


def _read_memory(project_path: str, filename: str | None) -> ToolResult:
    if not filename:
        return ToolError("filename is required for memory read.", title="Memory read")

    entries = scan_memory_files(project_path)
    entry = _find_entry(entries, filename)
    if not entry:
        return ToolError(f"Memory '{filename}' not found.", title="Memory read")

    text = (
        f"---\nname: {entry.name}\ndescription: {entry.description}\ntype: {entry.memory_type}\n---\n\n"
        f"{entry.content}"
    )
    return ToolOk(text, title=f"Memory: {entry.filename}", metadata={"path": entry.path, "filename": entry.filename})


def _write_memory(project_path: str, params: MemoryParams) -> ToolResult:
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

    path = save_memory(
        project_path,
        name=params.name or "",
        description=params.description or "",
        memory_type=params.memory_type or "project",
        content=params.content or "",
    )
    return ToolOk(f"Saved memory: {path}", title="Memory write", metadata={"path": path})


def _update_memory(project_path: str, params: MemoryParams) -> ToolResult:
    if not params.filename:
        return ToolError("filename is required for memory update.", title="Memory update")

    path = update_memory(
        project_path,
        params.filename,
        name=params.name,
        description=params.description,
        memory_type=params.memory_type,
        content=params.content,
    )
    if not path:
        return ToolError(f"Memory '{params.filename}' not found.", title="Memory update")
    return ToolOk(f"Updated memory: {path}", title="Memory update", metadata={"path": path})


def _delete_memory(project_path: str, filename: str | None) -> ToolResult:
    if not filename:
        return ToolError("filename is required for memory delete.", title="Memory delete")
    if not delete_memory(project_path, filename):
        return ToolError(f"Memory '{filename}' not found or could not be deleted.", title="Memory delete")
    return ToolOk(f"Deleted memory: {filename}", title="Memory delete", metadata={"filename": filename})


def _find_entry(entries: list[Any], filename: str) -> Any | None:
    normalized = filename.removesuffix(".md")
    for entry in entries:
        if filename in {entry.filename, entry.path, entry.name} or normalized == entry.relative_path:
            return entry
    return None


tool = MemoryTool()
