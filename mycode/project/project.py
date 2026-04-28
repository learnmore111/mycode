from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from mycode.project.instance import ProjectInfo
from mycode.util import log as logmod

logger = logmod.create(service="project")
GLOBAL_ID = "global"


async def _git(args: list[str], cwd: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=cwd)
        stdout, _ = await proc.communicate()
        return proc.returncode or 0, stdout.decode(errors="replace").strip()
    except Exception:
        return 1, ""


async def from_directory(directory: str) -> ProjectInfo:
    """Discover project info from a directory by finding the git root."""
    directory = str(Path(directory).resolve())

    # Look for .git
    current = directory
    dotgit = None
    while True:
        candidate = os.path.join(current, ".git")
        if os.path.exists(candidate):
            dotgit = candidate
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    if not dotgit:
        return ProjectInfo(id=GLOBAL_ID, worktree=directory)

    worktree = os.path.dirname(dotgit)
    if not shutil.which("git"):
        return ProjectInfo(id=GLOBAL_ID, worktree=worktree, vcs=None)

    # Get root commit as project ID
    code, text = await _git(["rev-list", "--max-parents=0", "HEAD"], cwd=worktree)
    if code != 0 or not text:
        return ProjectInfo(id=GLOBAL_ID, worktree=worktree, vcs="git")

    roots = sorted([line.strip() for line in text.split("\n") if line.strip()])
    project_id = roots[0] if roots else GLOBAL_ID

    # Get the actual top-level
    code2, toplevel = await _git(["rev-parse", "--show-toplevel"], cwd=worktree)
    if code2 == 0 and toplevel:
        worktree = toplevel

    logger.info("discovered project", id=project_id, worktree=worktree)
    return ProjectInfo(id=project_id, worktree=worktree, vcs="git")
