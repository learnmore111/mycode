"""Tests for extended tools: question, todo, skill, webfetch, websearch."""
import os
import tempfile

import pytest

import opencode.project.instance as inst
from opencode.tool.base import ToolContext
from opencode.tool.question import tool as question_tool
from opencode.tool.registry import all_tools, register_builtins
from opencode.tool.skill import tool as skill_tool
from opencode.tool.todo import tool as todo_tool


def _ctx(sid: str = "test") -> ToolContext:
    return ToolContext(session_id=sid, message_id="m1", agent="build")

@pytest.mark.asyncio
async def test_question():
    result = await question_tool.execute(
        {"question": "What language?", "options": ["Python", "Go"]}, _ctx())
    assert "What language?" in result.output
    assert "Python" in result.output
    assert result.metadata["awaiting_response"]

@pytest.mark.asyncio
async def test_todo():
    result = await todo_tool.execute({
        "todos": [
            {"id": "1", "content": "Setup project", "status": "completed"},
            {"id": "2", "content": "Write tests", "status": "in_progress"},
        ],
        "merge": False,
    }, _ctx("todo_test"))
    assert "Setup project" in result.output
    assert "✅" in result.output
    assert "🔶" in result.output

@pytest.mark.asyncio
async def test_todo_merge():
    ctx = _ctx("merge_test")
    await todo_tool.execute({"todos": [{"id": "1", "content": "A", "status": "pending"}]}, ctx)
    result = await todo_tool.execute({
        "todos": [{"id": "1", "content": "A", "status": "completed"}], "merge": True,
    }, ctx)
    assert "✅" in result.output

@pytest.mark.asyncio
async def test_skill_not_found():
    with tempfile.TemporaryDirectory() as d:
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="t", worktree=d)))
        try:
            result = await skill_tool.execute({"name": "nonexistent"}, _ctx())
            assert not result.metadata["found"]
        finally:
            token.reset()

@pytest.mark.asyncio
async def test_skill_found():
    with tempfile.TemporaryDirectory() as d:
        skill_dir = os.path.join(d, ".opencode", "skills")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "python.md"), "w") as f:
            f.write("# Python Skill\nUse type hints everywhere.")
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="t", worktree=d)))
        try:
            result = await skill_tool.execute({"name": "python"}, _ctx())
            assert result.metadata["found"]
            assert "type hints" in result.output
        finally:
            token.reset()

def test_all_builtins_registered():
    from opencode.tool.registry import clear
    clear()
    register_builtins()
    tools = all_tools()
    ids = {t.id for t in tools}
    expected = {"bash", "read", "edit", "write", "glob", "grep", "task", "webfetch", "websearch", "question", "todo", "skill"}
    assert expected.issubset(ids), f"Missing: {expected - ids}"
