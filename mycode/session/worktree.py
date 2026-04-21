"""Git worktree lifecycle management for isolated sub-agent execution.

Provides create/diff/apply/cleanup operations for temporary git worktrees
used by the isolated sub-agent mode.
"""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from mycode.util import log as logmod
from mycode.util.git_async import git, git_available

logger = logmod.create(service="session.worktree")

WORKTREE_DIR = ".mycode/worktrees"


@dataclass
class WorktreeInfo:
    """Information about a created worktree."""
    path: str          # Absolute path to the worktree directory
    branch: str        # Branch name created for this worktree
    base_commit: str   # The commit this worktree was branched from
    task_id: str       # Unique identifier for this worktree
    project_dir: str   # The original project directory (avoids fragile path math)


async def create_worktree(project_dir: str, task_id: str | None = None) -> WorktreeInfo | None:
    """Create a temporary git worktree for isolated sub-agent execution.

    Args:
        project_dir: The main project directory (must be a git repo).
        task_id: Optional unique identifier. Auto-generated if not provided.

    Returns:
        WorktreeInfo on success, None if git is unavailable or not a git repo.
    """
    if not git_available():
        logger.warn("git not available, cannot create worktree")
        return None

    # Verify this is a git repo
    code, _, _ = await git(["rev-parse", "--is-inside-work-tree"], cwd=project_dir)
    if code != 0:
        logger.warn("not a git repo, cannot create worktree", path=project_dir)
        return None

    # Get current HEAD commit
    code, head_out, _ = await git(["rev-parse", "HEAD"], cwd=project_dir)
    if code != 0:
        # No commits yet — initialize with an empty commit
        code, _, err = await git(
            ["commit", "--allow-empty", "-m", "initial (subagent worktree base)"],
            cwd=project_dir,
        )
        if code != 0:
            logger.error("failed to create initial commit for worktree", error=err)
            return None
        code, head_out, _ = await git(["rev-parse", "HEAD"], cwd=project_dir)
        if code != 0:
            return None

    base_commit = head_out.strip()

    if not task_id:
        task_id = uuid.uuid4().hex[:12]

    branch_name = f"subagent/{task_id}"
    worktree_path = os.path.join(project_dir, WORKTREE_DIR, task_id)

    Path(worktree_path).parent.mkdir(parents=True, exist_ok=True)

    code, _, err = await git(
        ["worktree", "add", "-b", branch_name, worktree_path, base_commit],
        cwd=project_dir,
    )
    if code != 0:
        logger.error("failed to create worktree", error=err, path=worktree_path)
        return None

    logger.info("worktree created", path=worktree_path, branch=branch_name)
    return WorktreeInfo(
        path=worktree_path,
        branch=branch_name,
        base_commit=base_commit,
        task_id=task_id,
        project_dir=project_dir,
    )


async def stage_and_collect(worktree: WorktreeInfo) -> tuple[str, list[str]]:
    """Stage all changes and collect both diff and changed file list in one pass.

    Returns:
        (diff_text, changed_file_list) — avoids running `git add -A` twice.
    """
    code, _, _ = await git(["add", "-A"], cwd=worktree.path)
    if code != 0:
        return "", []

    # Get diff
    code, diff_out, _ = await git(
        ["diff", "--cached", "--no-ext-diff", worktree.base_commit],
        cwd=worktree.path,
    )
    diff = diff_out.strip() if code == 0 else ""

    # Get changed file names
    code, names_out, _ = await git(
        ["diff", "--cached", "--name-only", worktree.base_commit],
        cwd=worktree.path,
    )
    changed = [f.strip() for f in names_out.strip().split("\n") if f.strip()] if code == 0 and names_out.strip() else []

    return diff, changed


async def apply_diff_text(diff: str, target_dir: str) -> bool:
    """Apply a diff string to the target directory.

    Accepts the diff text directly (caller already has it) to avoid redundant git ops.

    Returns:
        True if the patch was applied successfully, False otherwise.
    """
    if not diff:
        return True

    import asyncio

    # Try with --3way first for better merge handling
    proc = await asyncio.create_subprocess_exec(
        "git", "apply", "--3way",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=target_dir,
    )
    _, stderr = await proc.communicate(input=diff.encode())
    code = proc.returncode or 0

    if code != 0:
        # Fallback without --3way
        proc = await asyncio.create_subprocess_exec(
            "git", "apply",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=target_dir,
        )
        _, stderr = await proc.communicate(input=diff.encode())
        code = proc.returncode or 0

    if code != 0:
        logger.error("failed to apply diff", error=stderr.decode(errors="replace"))
        return False

    logger.info("diff applied successfully")
    return True


async def cleanup_worktree(worktree: WorktreeInfo) -> None:
    """Remove a worktree and its associated branch.

    Safe to call even if the worktree was already removed.
    """
    project_dir = worktree.project_dir

    code, _, _ = await git(["worktree", "remove", "--force", worktree.path], cwd=project_dir)
    if code != 0:
        if os.path.exists(worktree.path):
            shutil.rmtree(worktree.path, ignore_errors=True)
        logger.debug("worktree force-removed", path=worktree.path)

    await git(["branch", "-D", worktree.branch], cwd=project_dir)
    await git(["worktree", "prune"], cwd=project_dir)

    logger.info("worktree cleaned up", task_id=worktree.task_id, branch=worktree.branch)
