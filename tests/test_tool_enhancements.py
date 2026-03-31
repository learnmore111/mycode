"""Tests for tool enhancements: path safety, atomic write, capability declarations,
bash stderr separation, registry sorting, encoding detection.
"""
from __future__ import annotations

import os
import tempfile

import pytest

import opencode.project.instance as inst
from opencode.tool.base import (
    ToolContext,
    ToolInfo,
    atomic_write,
    resolve_tool_path,
    validate_path_safety,
)
from opencode.tool.bash import tool as bash_tool
from opencode.tool.edit import tool as edit_tool
from opencode.tool.glob_tool import tool as glob_tool
from opencode.tool.grep import tool as grep_tool
from opencode.tool.read import tool as read_tool
from opencode.tool.registry import clear, register, register_builtins, to_llm_tools
from opencode.tool.webfetch import tool as webfetch_tool
from opencode.tool.write import tool as write_tool


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


# ── Path safety validation ─────────────────────────────────────────


def test_path_safety_within_project(tmp_path):
    assert validate_path_safety("src/main.py", str(tmp_path)) is None


def test_path_safety_absolute_within_project(tmp_path):
    p = os.path.join(str(tmp_path), "src", "main.py")
    assert validate_path_safety(p, str(tmp_path)) is None


def test_path_safety_traversal_blocked(tmp_path):
    error = validate_path_safety("../../etc/passwd", str(tmp_path))
    assert error is not None
    assert "outside the project" in error


def test_path_safety_absolute_outside_blocked(tmp_path):
    error = validate_path_safety("/etc/passwd", str(tmp_path))
    assert error is not None
    assert "outside the project" in error


def test_resolve_tool_path_safe(tmp_path):
    full, err = resolve_tool_path("file.txt", str(tmp_path))
    assert err is None
    assert full == os.path.join(str(tmp_path), "file.txt")


def test_resolve_tool_path_unsafe(tmp_path):
    full, err = resolve_tool_path("/etc/hosts", str(tmp_path))
    assert err is not None


# ── Atomic write ───────────────────────────────────────────────────


def test_atomic_write_basic(tmp_path):
    p = os.path.join(str(tmp_path), "test.txt")
    atomic_write(p, "hello world")
    assert open(p).read() == "hello world"


def test_atomic_write_creates_parents(tmp_path):
    p = os.path.join(str(tmp_path), "a", "b", "c.txt")
    atomic_write(p, "deep")
    assert open(p).read() == "deep"


def test_atomic_write_overwrites(tmp_path):
    p = os.path.join(str(tmp_path), "test.txt")
    atomic_write(p, "first")
    atomic_write(p, "second")
    assert open(p).read() == "second"


# ── Capability declarations ────────────────────────────────────────


def test_read_tool_is_read_only():
    assert read_tool.is_read_only() is True


def test_write_tool_is_destructive():
    assert write_tool.is_destructive() is True


def test_write_tool_not_concurrency_safe():
    assert write_tool.is_concurrency_safe() is False


def test_edit_tool_not_concurrency_safe():
    assert edit_tool.is_concurrency_safe() is False


def test_bash_tool_is_concurrency_safe():
    assert bash_tool.is_concurrency_safe() is True


def test_grep_tool_is_read_only():
    assert grep_tool.is_read_only() is True


def test_glob_tool_is_read_only():
    assert glob_tool.is_read_only() is True


def test_webfetch_tool_is_read_only():
    assert webfetch_tool.is_read_only() is True


def test_default_tool_info_capabilities():
    """Default ToolInfo: not read-only, not destructive, concurrency-safe."""
    from opencode.tool.base import CallableTool, ToolResult
    from pydantic import BaseModel

    class P(BaseModel):
        x: str = ""

    class T(CallableTool[P]):
        id = "test_default"
        description = "test"
        async def call(self, params, ctx):
            return ToolResult()

    t = T()
    assert t.is_read_only() is False
    assert t.is_destructive() is False
    assert t.is_concurrency_safe() is True
    assert t.is_enabled() is True


# ── Bash stderr separation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bash_stderr_separation(_project):
    result = await bash_tool.execute({"command": "echo out; echo err >&2"}, _ctx())
    assert "out" in result.output
    assert "stderr" in result.output
    assert "err" in result.output


@pytest.mark.asyncio
async def test_bash_env_vars(_project):
    result = await bash_tool.execute({
        "command": "echo $MY_VAR",
        "env": {"MY_VAR": "hello_from_env"},
    }, _ctx())
    assert "hello_from_env" in result.output


@pytest.mark.asyncio
async def test_bash_has_stderr_metadata(_project):
    result = await bash_tool.execute({"command": "echo err >&2"}, _ctx())
    assert result.metadata.get("has_stderr") is True


@pytest.mark.asyncio
async def test_bash_no_stderr_metadata(_project):
    result = await bash_tool.execute({"command": "echo hello"}, _ctx())
    assert result.metadata.get("has_stderr") is False


# ── Write path safety ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_blocks_outside_project(_project):
    result = await write_tool.execute({
        "file_path": "/etc/test_opencode_blocked",
        "content": "should not write",
    }, _ctx())
    assert result.is_error
    assert "outside the project" in result.output


@pytest.mark.asyncio
async def test_write_blocks_traversal(_project):
    result = await write_tool.execute({
        "file_path": "../../should_not_exist.txt",
        "content": "nope",
    }, _ctx())
    assert result.is_error
    assert "outside the project" in result.output


@pytest.mark.asyncio
async def test_write_allows_within_project(_project):
    result = await write_tool.execute({
        "file_path": "safe.txt",
        "content": "safe content",
    }, _ctx())
    assert result.metadata["success"] is True


# ── Edit path safety ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_blocks_outside_project(_project):
    result = await edit_tool.execute({
        "file_path": "/etc/passwd",
        "old_string": "root",
        "new_string": "hacked",
    }, _ctx())
    assert result.is_error
    assert "outside the project" in result.output


@pytest.mark.asyncio
async def test_edit_suggests_write_for_missing_file(_project):
    result = await edit_tool.execute({
        "file_path": "does_not_exist.py",
        "old_string": "x",
        "new_string": "y",
    }, _ctx())
    assert result.is_error
    assert "write tool" in result.output.lower()


# ── Read path safety + encoding ───────────────────────────────────


@pytest.mark.asyncio
async def test_read_blocks_outside_project(_project):
    result = await read_tool.execute({
        "file_path": "/etc/passwd",
    }, _ctx())
    assert result.is_error
    assert "outside the project" in result.output


@pytest.mark.asyncio
async def test_read_encoding_in_metadata(_project):
    p = os.path.join(str(_project), "test.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("hello world")
    result = await read_tool.execute({"file_path": "test.txt"}, _ctx())
    assert "encoding" in result.metadata


@pytest.mark.asyncio
async def test_read_binary_detection(_project):
    p = os.path.join(str(_project), "binary.bin")
    with open(p, "wb") as f:
        f.write(b"\x00\x01\x02\x03\x04")
    result = await read_tool.execute({"file_path": "binary.bin"}, _ctx())
    assert result.is_error
    assert "binary" in result.output.lower()


@pytest.mark.asyncio
async def test_read_image_detection(_project):
    p = os.path.join(str(_project), "photo.png")
    with open(p, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")  # PNG header
    result = await read_tool.execute({"file_path": "photo.png"}, _ctx())
    assert not result.is_error
    assert "image" in result.output.lower()


# ── Registry sorting ──────────────────────────────────────────────


def test_registry_tools_sorted():
    clear()
    register_builtins()
    tools = to_llm_tools()
    names = [t["function"]["name"] for t in tools]
    assert names == sorted(names), f"Tools not sorted: {names}"
    clear()


def test_registry_sort_stability():
    """Calling to_llm_tools() twice should return identical order."""
    clear()
    register_builtins()
    first = [t["function"]["name"] for t in to_llm_tools()]
    second = [t["function"]["name"] for t in to_llm_tools()]
    assert first == second
    clear()
