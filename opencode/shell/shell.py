"""Shell utilities — process management and shell detection. Equivalent to src/shell/shell.ts."""
from __future__ import annotations
import asyncio, os, platform, shutil, signal
from pathlib import Path

SIGKILL_TIMEOUT = 0.2


async def kill_tree(pid: int) -> None:
    """Kill a process and all its descendants."""
    if platform.system() == "Windows":
        proc = await asyncio.create_subprocess_exec(
            "taskkill", "/pid", str(pid), "/f", "/t",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return
    try:
        os.killpg(pid, signal.SIGTERM)
        await asyncio.sleep(SIGKILL_TIMEOUT)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(SIGKILL_TIMEOUT)
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


_BLACKLIST = {"fish", "nu"}


def _fallback() -> str:
    if platform.system() == "Windows":
        git = shutil.which("git")
        if git:
            bash = str(Path(git).parent.parent / "bin" / "bash.exe")
            if Path(bash).exists():
                return bash
        return os.environ.get("COMSPEC", "cmd.exe")
    if platform.system() == "Darwin":
        return "/bin/zsh"
    return shutil.which("bash") or "/bin/sh"


def preferred() -> str:
    """User's preferred shell ($SHELL)."""
    s = os.environ.get("SHELL")
    return s if s else _fallback()


def acceptable() -> str:
    """Acceptable shell (excludes fish/nu)."""
    s = os.environ.get("SHELL")
    if s and Path(s).name not in _BLACKLIST:
        return s
    return _fallback()
