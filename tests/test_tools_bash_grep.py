"""Tests for bash and grep tools."""
import os
import pytest
import mycode.project.instance as inst
from mycode.tool.base import ToolContext
from mycode.tool.bash import tool as bash_tool
from mycode.tool.grep import tool as grep_tool


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="m1", agent="build")


@pytest.fixture
def _project(tmp_path):
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path), worktree=str(tmp_path),
        project=inst.ProjectInfo(id="test", worktree=str(tmp_path)),
    ))
    yield tmp_path
    token.reset()


@pytest.mark.asyncio
async def test_bash_echo(_project):
    result = await bash_tool.execute({"command": "echo hello"}, _ctx())
    assert "hello" in result.output
    assert result.metadata["exit_code"] == 0


@pytest.mark.asyncio
async def test_bash_exit_code(_project):
    result = await bash_tool.execute({"command": "exit 1"}, _ctx())
    assert result.metadata["exit_code"] == 1
    assert "Exit code: 1" in result.output


@pytest.mark.asyncio
async def test_bash_timeout(_project):
    result = await bash_tool.execute({"command": "sleep 10", "timeout": 500}, _ctx())
    assert result.metadata.get("timeout") is True


@pytest.mark.asyncio
async def test_bash_schema():
    schema = bash_tool.parameters_schema()
    assert "command" in schema["properties"]
    assert "command" in schema["required"]


@pytest.mark.asyncio
async def test_grep_basic(_project):
    with open(os.path.join(str(_project), "test.txt"), "w") as f:
        f.write("hello world\nfoo bar\nhello again\n")
    result = await grep_tool.execute({"pattern": "hello", "path": "."}, _ctx())
    assert "hello" in result.output or result.metadata.get("matches", 0) >= 1


@pytest.mark.asyncio
async def test_grep_no_matches(_project):
    with open(os.path.join(str(_project), "test.txt"), "w") as f:
        f.write("abc\ndef\n")
    result = await grep_tool.execute({"pattern": "zzzznotfound"}, _ctx())
    assert "No matches" in result.output or result.metadata.get("matches", 0) == 0


@pytest.mark.asyncio
async def test_grep_schema():
    schema = grep_tool.parameters_schema()
    assert "pattern" in schema["properties"]
    assert "pattern" in schema["required"]
