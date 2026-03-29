"""Todo tool — manage a task list. Equivalent to src/tool/todo.ts."""
from __future__ import annotations
import json
from typing import Any
from opencode.tool.base import ToolInfo, ToolResult, ToolContext

# In-memory todo storage per session
_todos: dict[str, list[dict[str, Any]]] = {}


class TodoTool(ToolInfo):
    id = "todo"
    description = "Create and manage a todo list to track progress on multi-step tasks."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                        },
                        "required": ["id", "content", "status"],
                    },
                    "description": "List of todo items",
                },
                "merge": {
                    "type": "boolean",
                    "description": "If true, merge with existing todos. If false, replace.",
                },
            },
            "required": ["todos"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        items = args["todos"]
        merge = args.get("merge", True)

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

        return ToolResult(
            title="Todo list updated",
            output="\n".join(lines) if lines else "Empty todo list.",
            metadata={"count": len(current)},
        )


tool = TodoTool()
