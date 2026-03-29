"""Tests for file, snapshot, project, shell modules."""
import os, tempfile
import pytest
from opencode.shell.shell import preferred, acceptable
from opencode.project.project import from_directory
from opencode.file.file import read, list_dir
import opencode.project.instance as inst


def test_shell_preferred():
    s = preferred()
    assert s and os.path.basename(s) not in ("fish", "nu")


def test_shell_acceptable():
    s = acceptable()
    assert s


@pytest.mark.asyncio
async def test_project_from_directory():
    with tempfile.TemporaryDirectory() as d:
        info = await from_directory(d)
        assert info.worktree == os.path.realpath(d) or info.worktree == d
        assert info.id  # "global" or a git hash


@pytest.mark.asyncio
async def test_file_read():
    with tempfile.TemporaryDirectory() as d:
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="t", worktree=d)))
        try:
            open(os.path.join(d, "test.txt"), "w").write("hello world")
            result = await read("test.txt")
            assert result["type"] == "text"
            assert "hello world" in result["content"]
        finally:
            token.reset()


@pytest.mark.asyncio
async def test_file_read_missing():
    with tempfile.TemporaryDirectory() as d:
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="t", worktree=d)))
        try:
            result = await read("nope.txt")
            assert result["content"] == ""
        finally:
            token.reset()


@pytest.mark.asyncio
async def test_list_dir():
    with tempfile.TemporaryDirectory() as d:
        token = inst.set_context(inst.InstanceContext(
            directory=d, worktree=d, project=inst.ProjectInfo(id="t", worktree=d)))
        try:
            os.makedirs(os.path.join(d, "src"))
            open(os.path.join(d, "main.py"), "w").close()
            entries = await list_dir()
            names = [e["name"] for e in entries]
            assert "src" in names
            assert "main.py" in names
        finally:
            token.reset()
