"""Tests for the snapshot system."""
import os
import shutil
import pytest
from opencode.snapshot.snapshot import Snapshot


@pytest.fixture
def _tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr("opencode.util.paths.GlobalPaths.data", staticmethod(lambda: tmp_path / "data"))
    return tmp_path


@pytest.fixture
def worktree(tmp_path):
    wt = tmp_path / "project"
    wt.mkdir()
    (wt / "file.txt").write_text("hello")
    return str(wt)


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
@pytest.mark.asyncio
async def test_init_and_track(_tmp_data, worktree):
    snap = Snapshot("proj1", worktree)
    tree_hash = await snap.track()
    assert tree_hash is not None
    assert len(tree_hash) == 40  # SHA1 hash


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
@pytest.mark.asyncio
async def test_diff(_tmp_data, worktree):
    snap = Snapshot("proj2", worktree)
    h1 = await snap.track()
    assert h1 is not None
    # Modify file
    with open(os.path.join(worktree, "file.txt"), "w") as f:
        f.write("modified")
    diff = await snap.diff(h1)
    assert "modified" in diff or "file.txt" in diff


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
@pytest.mark.asyncio
async def test_patch(_tmp_data, worktree):
    snap = Snapshot("proj3", worktree)
    h1 = await snap.track()
    assert h1 is not None
    with open(os.path.join(worktree, "new.txt"), "w") as f:
        f.write("new file")
    result = await snap.patch(h1)
    assert "files" in result
    assert any("new.txt" in f for f in result["files"])
