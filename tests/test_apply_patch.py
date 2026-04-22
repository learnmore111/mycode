"""Tests for apply_patch tool."""

from __future__ import annotations

from pathlib import Path

import pytest

import mycode.project.instance as inst
from mycode.tool.apply_patch import ApplyPatchParams, tool
from mycode.tool.base import ToolContext


@pytest.fixture()
def workdir(tmp_path: Path):
    token = inst.set_context(inst.InstanceContext(
        directory=str(tmp_path),
        worktree=str(tmp_path),
        project=inst.ProjectInfo(id="global", worktree=str(tmp_path)),
    ))
    yield tmp_path
    token.reset()


def _ctx() -> ToolContext:
    return ToolContext(session_id="s", message_id="m", agent="build", call_id="c")


async def test_add_file(workdir: Path):
    patch = (
        "*** Begin Patch\n"
        "*** Add File: hello.py\n"
        "+print('hi')\n"
        "*** End Patch\n"
    )
    result = await tool.call(ApplyPatchParams(patch=patch), _ctx())
    assert not result.is_error, result.output
    assert (workdir / "hello.py").read_text() == "print('hi')\n"


async def test_update_file(workdir: Path):
    target = workdir / "sample.py"
    target.write_text("def greet():\n    return 'hello'\n")

    patch = (
        "*** Begin Patch\n"
        "*** Update File: sample.py\n"
        "@@ def greet\n"
        "-    return 'hello'\n"
        "+    return 'hello, world'\n"
        "*** End Patch\n"
    )
    result = await tool.call(ApplyPatchParams(patch=patch), _ctx())
    assert not result.is_error, result.output
    assert target.read_text() == "def greet():\n    return 'hello, world'\n"


async def test_delete_file(workdir: Path):
    target = workdir / "gone.txt"
    target.write_text("bye\n")
    patch = (
        "*** Begin Patch\n"
        "*** Delete File: gone.txt\n"
        "*** End Patch\n"
    )
    result = await tool.call(ApplyPatchParams(patch=patch), _ctx())
    assert not result.is_error, result.output
    assert not target.exists()


async def test_multi_file_atomic_rollback(workdir: Path):
    # Arrange two files, craft a patch whose second hunk will fail so the
    # first hunk's write must be rolled back.
    (workdir / "a.py").write_text("old-a\n")
    (workdir / "b.py").write_text("old-b\n")

    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.py\n"
        "-old-a\n"
        "+new-a\n"
        "*** Update File: b.py\n"
        "-this-wont-match\n"   # will fail validation in phase 1
        "+never-applied\n"
        "*** End Patch\n"
    )
    result = await tool.call(ApplyPatchParams(patch=patch), _ctx())
    assert result.is_error
    # Phase-1 validation aborts before any write — so a.py must be
    # untouched, not mid-applied.
    assert (workdir / "a.py").read_text() == "old-a\n"
    assert (workdir / "b.py").read_text() == "old-b\n"


async def test_add_existing_file_rejected(workdir: Path):
    (workdir / "dup.py").write_text("already here\n")
    patch = (
        "*** Begin Patch\n"
        "*** Add File: dup.py\n"
        "+overwrite\n"
        "*** End Patch\n"
    )
    result = await tool.call(ApplyPatchParams(patch=patch), _ctx())
    assert result.is_error
    assert "already exists" in result.output


async def test_missing_begin_marker(workdir: Path):
    result = await tool.call(ApplyPatchParams(patch="*** Add File: x\n+y\n"), _ctx())
    assert result.is_error
    assert "Begin Patch" in result.output


async def test_path_traversal_blocked(workdir: Path):
    patch = (
        "*** Begin Patch\n"
        "*** Add File: ../../outside.py\n"
        "+should not write\n"
        "*** End Patch\n"
    )
    result = await tool.call(ApplyPatchParams(patch=patch), _ctx())
    assert result.is_error
    assert "Path not allowed" in result.output
