"""Tests for the tool system."""
import os
import tempfile

import pytest

from mycode.tool.base import ToolContext
from mycode.tool.edit import tool as edit_tool
from mycode.tool.glob_tool import tool as glob_tool
from mycode.tool.read import tool as read_tool
from mycode.tool.registry import all_tools, to_llm_tools
from mycode.tool.write import tool as write_tool


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="test", agent="build")

@pytest.mark.asyncio
async def test_write_and_read():
    with tempfile.TemporaryDirectory() as d:
        # Patch instance
        import mycode.project.instance as inst
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="test", worktree=d),
        ))
        try:
            result = await write_tool.execute({"file_path": "hello.txt", "content": "hello\nworld"}, _ctx())
            assert result.metadata["success"]
            result = await read_tool.execute({"file_path": "hello.txt"}, _ctx())
            assert "hello" in result.output
            assert "world" in result.output
        finally:
            token.reset()

@pytest.mark.asyncio
async def test_edit():
    with tempfile.TemporaryDirectory() as d:
        import mycode.project.instance as inst
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="test", worktree=d),
        ))
        try:
            await write_tool.execute({"file_path": "test.py", "content": "def hello():\n    return 'old'"}, _ctx())
            result = await edit_tool.execute({
                "file_path": "test.py", "old_string": "return 'old'", "new_string": "return 'new'",
            }, _ctx())
            assert result.metadata["success"]
            content = open(os.path.join(d, "test.py")).read()
            assert "return 'new'" in content
        finally:
            token.reset()

@pytest.mark.asyncio
async def test_glob():
    with tempfile.TemporaryDirectory() as d:
        import mycode.project.instance as inst
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="test", worktree=d),
        ))
        try:
            os.makedirs(os.path.join(d, "src"))
            open(os.path.join(d, "src", "a.py"), "w").close()
            open(os.path.join(d, "src", "b.py"), "w").close()
            result = await glob_tool.execute({"pattern": "**/*.py"}, _ctx())
            assert "a.py" in result.output
            assert result.metadata["count"] == 2
        finally:
            token.reset()

def test_registry():
    from mycode.tool.registry import clear, register_builtins
    clear()  # Reset for clean test
    register_builtins()
    tools = all_tools()
    assert len(tools) >= 4  # bash, read, edit, write, glob, grep
    llm_tools = to_llm_tools()
    assert all(t["type"] == "function" for t in llm_tools)

def test_tool_schema():
    schema = read_tool.parameters_schema()
    assert schema["type"] == "object"
    assert "file_path" in schema["properties"]
