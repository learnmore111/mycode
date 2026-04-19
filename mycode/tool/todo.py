from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from pydantic import BaseModel, Field

from mycode.tool.base import CallableTool, ToolContext, ToolOk, ToolResult
from mycode.util import log as logmod

logger = logmod.create(service="tool.todo")

# In-memory todo storage per session.
# Uses OrderedDict with a max size to prevent unbounded growth
# when sessions are never explicitly deleted.
_MAX_SESSIONS = 500
_todos: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
_todos_lock = asyncio.Lock()


def get_todos(session_id: str) -> list[dict[str, Any]]:
    """Get todo items for a session (returns a copy)."""
    return list(_todos.get(session_id, []))


async def set_todos(session_id: str, items: list[dict[str, Any]]) -> None:
    """Replace todo items for a session (async-safe)."""
    async with _todos_lock:
        _todos[session_id] = items
        _todos.move_to_end(session_id)
        # Evict oldest sessions if over limit
        while len(_todos) > _MAX_SESSIONS:
            evicted_id, _ = _todos.popitem(last=False)
            logger.debug("todos evicted (LRU)", session_id=evicted_id)
    logger.debug("todos updated", session_id=session_id, count=len(items))


def clear_todos(session_id: str) -> None:
    """Remove all todos for a session."""
    removed = _todos.pop(session_id, None)
    if removed:
        logger.debug("todos cleared", session_id=session_id)


class TodoItem(BaseModel):
    """A single todo item."""
    id: str
    content: str
    status: str = Field(description="One of: pending, in_progress, completed, cancelled")


class TodoParams(BaseModel):
    """Parameters for the todo tool."""
    todos: list[TodoItem] = Field(description="List of todo items")
    merge: bool = Field(default=True, description="If true, merge with existing todos. If false, replace.")


class TodoTool(CallableTool[TodoParams]):
    id = "todo"
    description = "Create and manage a todo list to track progress on multi-step tasks."

    async def call(self, params: TodoParams, ctx: ToolContext) -> ToolResult:
        items = [item.model_dump() for item in params.todos]
        merge = params.merge

        if merge:
            existing_items = get_todos(ctx.session_id)
            if existing_items:
                existing = {t["id"]: t for t in existing_items}
                for item in items:
                    existing[item["id"]] = item
                await set_todos(ctx.session_id, list(existing.values()))
            else:
                await set_todos(ctx.session_id, list(items))
        else:
            await set_todos(ctx.session_id, list(items))

        current = get_todos(ctx.session_id)
        lines = []
        for t in current:
            icon = {"pending": "⬜", "in_progress": "🔶", "completed": "✅", "cancelled": "⬛"}.get(t["status"], "⬜")
            lines.append(f"{icon} [{t['id']}] {t['content']}")

        return ToolOk(
            "\n".join(lines) if lines else "Empty todo list.",
            title="Todo list updated",
            metadata={"count": len(current)},
        )


tool = TodoTool()
