"""Tests for the memory tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mycode.project.instance as inst
from mycode.tool.base import ToolContext
from mycode.tool.memory import tool as memory_tool

if TYPE_CHECKING:
    from pathlib import Path


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="m1", agent="build")


async def test_memory_tool_write_list_read_update_delete(tmp_path: Path):
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path),
        worktree=str(tmp_path),
        project=inst.ProjectInfo(id="test", worktree=str(tmp_path)),
    ))
    try:
        result = await memory_tool.execute({
            "action": "write",
            "name": "Concise replies",
            "description": "User prefers concise replies without repeated summaries",
            "memory_type": "feedback",
            "content": "Keep responses concise.\n\n**Why:** User asked for less repetition.\n**How to apply:** Avoid redundant wrap-ups.",
        }, _ctx())
        assert "Saved memory" in result.output

        result = await memory_tool.execute({"action": "list"}, _ctx())
        assert "MEMORY.md" in result.output
        assert "feedback_concise_replies.md" in result.output

        result = await memory_tool.execute({"action": "read", "filename": "feedback_concise_replies.md"}, _ctx())
        assert "Keep responses concise" in result.output

        result = await memory_tool.execute({
            "action": "update",
            "filename": "feedback_concise_replies.md",
            "description": "User prefers concise replies and no redundant summaries",
        }, _ctx())
        assert "Updated memory" in result.output

        result = await memory_tool.execute({"action": "delete", "filename": "feedback_concise_replies.md"}, _ctx())
        assert "Deleted memory" in result.output
    finally:
        token.reset()


async def test_memory_tool_read_only_capabilities():
    assert memory_tool.is_read_only({"action": "list"})
    assert memory_tool.is_read_only({"action": "read"})
    assert not memory_tool.is_read_only({"action": "write"})
