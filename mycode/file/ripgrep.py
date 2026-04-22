from __future__ import annotations

import asyncio
import contextlib
import shutil
from typing import TYPE_CHECKING

from mycode.util import log as logmod

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logmod.create(service="file.ripgrep")

# Default ripgrep timeout. Override via MYCODE_RG_TIMEOUT env var (seconds).
_DEFAULT_RG_TIMEOUT_S = 30

# Sentinel appended to search output when we had to stop early.
_TIMEOUT_FOOTER = "\n\n[search truncated: ripgrep exceeded {timeout}s, showing partial results]"


async def files(*, cwd: str) -> AsyncGenerator[str, None]:
    """List all non-ignored files using ripgrep --files. Falls back to find."""
    rg = shutil.which("rg")
    if rg:
        from mycode.file.ignore import RG_EXCLUDE_GLOBS
        cmd = [rg, "--files", "--hidden", "--glob", "!.git"]
        for glob_pattern in RG_EXCLUDE_GLOBS:
            cmd += ["--glob", glob_pattern]
    else:
        from mycode.file.ignore import IGNORED_DIRS
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
    """Search file contents. Returns ripgrep/grep output.

    Behaviour on timeout: we kill the subprocess, capture whatever output
    it produced before the timeout, and append a human-readable footer so
    the caller (and the LLM) knows the result is partial rather than
    truly empty. Raising TimeoutError would force the grep tool to
    surface an opaque failure, which was the previous behaviour.
    """
    import os

    timeout_s = _DEFAULT_RG_TIMEOUT_S
    raw_timeout = os.environ.get("MYCODE_RG_TIMEOUT")
    if raw_timeout:
        try:
            timeout_s = max(1, int(raw_timeout))
        except ValueError:
            logger.warn("invalid MYCODE_RG_TIMEOUT, using default", value=raw_timeout)

    rg = shutil.which("rg")
    if rg:
        from mycode.file.ignore import RG_EXCLUDE_GLOBS
        cmd = [rg, "-n", "--no-heading", "-m", str(max_count)]
        for glob_pattern in RG_EXCLUDE_GLOBS:
            cmd += ["--glob", glob_pattern]
        if glob:
            cmd += ["-g", glob]
        cmd.append(pattern)
    else:
        from mycode.file.ignore import IGNORED_DIRS
        cmd = ["grep", "-rn", "-m", str(max_count)]
        for d in sorted(IGNORED_DIRS):
            if "*" not in d:
                cmd += ["--exclude-dir", d]
        cmd.append(pattern)
        cmd.append(".")

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=cwd,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        logger.warn("ripgrep timeout", pattern=pattern[:80], timeout_s=timeout_s)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        # Drain any output that already made it to the pipe so the caller
        # still sees partial matches rather than a blank failure.
        partial: bytes = b""
        if proc.stdout is not None:
            with contextlib.suppress(Exception):
                partial = await asyncio.wait_for(proc.stdout.read(), timeout=1.0)
        with contextlib.suppress(Exception):
            await proc.wait()
        text = partial.decode("utf-8", errors="replace").strip()
        footer = _TIMEOUT_FOOTER.format(timeout=timeout_s)
        return (text + footer) if text else footer.lstrip("\n")
    return stdout.decode("utf-8", errors="replace").strip() if stdout else ""
