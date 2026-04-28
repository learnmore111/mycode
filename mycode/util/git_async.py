"""Shared async git subprocess utilities.

Used by both snapshot.py and session/worktree.py.
"""
from __future__ import annotations

import asyncio
import functools
import os
import shutil


@functools.lru_cache(maxsize=1)
def git_available() -> bool:
    """Check if git is available on the system (cached)."""
    return shutil.which("git") is not None


async def git(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a git command asynchronously.

    Args:
        args: Git subcommand and arguments (e.g. ["status", "--short"]).
        cwd: Working directory for the command.
        env: Extra environment variables to merge with os.environ.

    Returns:
        (returncode, stdout, stderr) tuple.
    """
    if not git_available():
        return 1, "", "git not found"
    merged_env = {**os.environ, **(env or {})} if env else None
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=merged_env,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
