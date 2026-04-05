from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from opencode.tool.base import CallableTool, ToolContext, ToolOk, ToolResult

# In-memory todo storage per session
_todos: dict[str, list[dict[str, Any]]] = {}


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

        if merge and ctx.session_id in _todos:
            existing = {t["id"]: t for t in _todos[ctx.session_id]}
            for item in items:
                existing[item["id"]] = item
            _todos[ctx.session_id] = list(existing.values())
        else:
            _todos[ctx.session_id] = list(items)

        current = _todos.get(ctx.session_id, [])
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
