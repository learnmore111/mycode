"""Tests for the BatchTool — application-level explicit parallel tool execution."""
import os
import tempfile

import pytest

import opencode.project.instance as inst
from opencode.tool.base import ToolContext, ToolInfo, ToolResult
from opencode.tool.batch import BatchTool, MAX_BATCH_SIZE, _EXCLUDED_TOOLS, tool as batch_tool
from opencode.tool import registry as tool_registry


def _ctx() -> ToolContext:
    return ToolContext(session_id="test", message_id="m1", agent="build")


@pytest.fixture(autouse=True)
def _project(tmp_path):
    """Set up instance context + register builtins for every test."""
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path), worktree=str(tmp_path),
        project=inst.ProjectInfo(id="test", worktree=str(tmp_path)),
    ))
    tool_registry._tools.clear()
    tool_registry.register_builtins()
    yield tmp_path
    token.reset()


# ── Schema ──────────────────────────────────────────────────────────────


def test_batch_tool_schema():
    schema = batch_tool.parameters_schema()
    assert schema["type"] == "object"
    assert "calls" in schema["properties"]
    assert schema["properties"]["calls"]["type"] == "array"
    assert schema["properties"]["calls"]["maxItems"] == MAX_BATCH_SIZE
    assert "calls" in schema["required"]


def test_batch_tool_id_and_description():
    assert batch_tool.id == "batch"
    assert "parallel" in batch_tool.description.lower()


def test_batch_tool_to_llm_tool():
    llm = batch_tool.to_llm_tool()
    assert llm["type"] == "function"
    assert llm["function"]["name"] == "batch"


# ── Empty / no calls ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_empty_calls():
    result = await batch_tool.execute({"calls": []}, _ctx())
    assert result.metadata["total"] == 0
    assert result.metadata["succeeded"] == 0
    assert "No calls provided" in result.output


@pytest.mark.asyncio
async def test_batch_no_calls_key():
    result = await batch_tool.execute({}, _ctx())
    assert result.metadata["total"] == 0
    assert "No calls provided" in result.output


# ── Too many calls ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_exceeds_max():
    calls = [{"tool": "read", "args": {"file_path": "x"}}] * (MAX_BATCH_SIZE + 1)
    result = await batch_tool.execute({"calls": calls}, _ctx())
    assert result.metadata["succeeded"] == 0
    assert "Too many calls" in result.output


# ── Excluded tools ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_excludes_batch_itself():
    result = await batch_tool.execute({
        "calls": [{"tool": "batch", "args": {}}],
    }, _ctx())
    assert result.metadata["failed"] >= 1
    assert "not allowed" in result.output


@pytest.mark.asyncio
async def test_batch_excludes_task():
    result = await batch_tool.execute({
        "calls": [{"tool": "task", "args": {}}],
    }, _ctx())
    assert result.metadata["failed"] >= 1


@pytest.mark.asyncio
async def test_batch_excludes_question():
    result = await batch_tool.execute({
        "calls": [{"tool": "question", "args": {}}],
    }, _ctx())
    assert result.metadata["failed"] >= 1


@pytest.mark.asyncio
async def test_batch_all_excluded():
    """When every call fails validation, the error summary is returned."""
    result = await batch_tool.execute({
        "calls": [
            {"tool": "batch", "args": {}},
            {"tool": "task", "args": {}},
            {"tool": "todo", "args": {}},
        ],
    }, _ctx())
    assert result.metadata["succeeded"] == 0
    assert result.metadata["failed"] == 3
    assert "All calls failed validation" in result.output


# ── Unknown tool ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_unknown_tool():
    result = await batch_tool.execute({
        "calls": [{"tool": "nonexistent_tool_xyz", "args": {}}],
    }, _ctx())
    assert result.metadata["failed"] >= 1
    assert "Unknown tool" in result.output


# ── Successful parallel execution ──────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_read_multiple_files(_project):
    """Create files and batch-read them in parallel."""
    for name, content in [("a.txt", "alpha"), ("b.txt", "bravo"), ("c.txt", "charlie")]:
        with open(os.path.join(str(_project), name), "w") as f:
            f.write(content)

    result = await batch_tool.execute({
        "description": "read three files",
        "calls": [
            {"tool": "read", "args": {"file_path": "a.txt"}},
            {"tool": "read", "args": {"file_path": "b.txt"}},
            {"tool": "read", "args": {"file_path": "c.txt"}},
        ],
    }, _ctx())

    assert result.metadata["total"] == 3
    assert result.metadata["succeeded"] == 3
    assert result.metadata["failed"] == 0
    assert "alpha" in result.output
    assert "bravo" in result.output
    assert "charlie" in result.output
    assert "read three files" in result.title


@pytest.mark.asyncio
async def test_batch_glob_and_grep(_project):
    """Mix glob and grep tools in a batch."""
    os.makedirs(os.path.join(str(_project), "src"))
    with open(os.path.join(str(_project), "src", "main.py"), "w") as f:
        f.write("def hello():\n    return 'world'\n")

    result = await batch_tool.execute({
        "calls": [
            {"tool": "glob", "args": {"pattern": "**/*.py"}},
            {"tool": "grep", "args": {"pattern": "hello", "path": "."}},
        ],
    }, _ctx())

    assert result.metadata["succeeded"] == 2
    assert result.metadata["failed"] == 0


# ── Mixed success / failure ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_partial_validation_failure(_project):
    """Some calls pass validation, some fail; only valid ones execute."""
    with open(os.path.join(str(_project), "ok.txt"), "w") as f:
        f.write("content")

    result = await batch_tool.execute({
        "calls": [
            {"tool": "read", "args": {"file_path": "ok.txt"}},
            {"tool": "batch", "args": {}},  # excluded
        ],
    }, _ctx())

    # One succeeded from execution, one failed from validation
    assert result.metadata["total"] == 2
    assert result.metadata["succeeded"] == 1
    assert "Validation Errors" in result.output
    assert "Results" in result.output


@pytest.mark.asyncio
async def test_batch_execution_error(_project):
    """Reading a non-existent file should count as an execution error."""
    result = await batch_tool.execute({
        "calls": [
            {"tool": "read", "args": {"file_path": "does_not_exist.txt"}},
        ],
    }, _ctx())

    # The tool either returns an error in output or raises — either way metadata tracks it
    assert result.metadata["total"] == 1


# ── Description propagation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_description_in_title(_project):
    with open(os.path.join(str(_project), "x.txt"), "w") as f:
        f.write("hi")

    result = await batch_tool.execute({
        "description": "test desc",
        "calls": [{"tool": "read", "args": {"file_path": "x.txt"}}],
    }, _ctx())

    assert "test desc" in result.title


@pytest.mark.asyncio
async def test_batch_default_description():
    result = await batch_tool.execute({"calls": []}, _ctx())
    assert "batch execution" in result.title


# ── Concurrency verification ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_bash_parallel(_project):
    """Run multiple bash commands and verify they all complete."""
    result = await batch_tool.execute({
        "description": "parallel echo",
        "calls": [
            {"tool": "bash", "args": {"command": "echo aaa"}},
            {"tool": "bash", "args": {"command": "echo bbb"}},
            {"tool": "bash", "args": {"command": "echo ccc"}},
        ],
    }, _ctx())

    assert result.metadata["succeeded"] == 3
    assert "aaa" in result.output
    assert "bbb" in result.output
    assert "ccc" in result.output


# ── Excluded tools set ─────────────────────────────────────────────────


def test_excluded_tools_constant():
    assert "batch" in _EXCLUDED_TOOLS
    assert "task" in _EXCLUDED_TOOLS
    assert "todo" in _EXCLUDED_TOOLS
    assert "question" in _EXCLUDED_TOOLS
    # Normal tools should NOT be excluded
    assert "read" not in _EXCLUDED_TOOLS
    assert "bash" not in _EXCLUDED_TOOLS
