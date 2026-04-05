from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


async def files(*, cwd: str) -> AsyncGenerator[str, None]:
    """List all non-ignored files using ripgrep --files. Falls back to find."""
    rg = shutil.which("rg")
    if rg:
        from opencode.file.ignore import RG_EXCLUDE_GLOBS
        cmd = [rg, "--files", "--hidden", "--glob", "!.git"]
        for glob_pattern in RG_EXCLUDE_GLOBS:
            cmd += ["--glob", glob_pattern]
    else:
        from opencode.file.ignore import IGNORED_DIRS
        cmd = ["find", ".", "-type", "f"]
        for d in sorted(IGNORED_DIRS):
            if "*" not in d:  # find -not -path doesn't support wildcards well
                cmd += ["-not", "-path", f"*/{d}/*"]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=cwd,
    )
    assert proc.stdout
    async for line in proc.stdout:
        path = line.decode("utf-8", errors="replace").strip()
        if path:
            yield path
    await proc.wait()


async def search(pattern: str, *, cwd: str, glob: str | None = None, max_count: int = 100) -> str:
    """Search file contents. Returns ripgrep/grep output."""
    rg = shutil.which("rg")
    if rg:
        from opencode.file.ignore import RG_EXCLUDE_GLOBS
        cmd = [rg, "-n", "--no-heading", "-m", str(max_count)]
        for glob_pattern in RG_EXCLUDE_GLOBS:
            cmd += ["--glob", glob_pattern]
        if glob:
            cmd += ["-g", glob]
        cmd.append(pattern)
    else:
        from opencode.file.ignore import IGNORED_DIRS
        cmd = ["grep", "-rn", "-m", str(max_count)]
        for d in sorted(IGNORED_DIRS):
            if "*" not in d:
                cmd += ["--exclude-dir", d]
        cmd.append(pattern)
        cmd.append(".")

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=cwd,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    return stdout.decode("utf-8", errors="replace").strip() if stdout else ""
