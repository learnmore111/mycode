"""Snapshot system — shadow git repo for undo/redo. Equivalent to src/snapshot/index.ts."""
from __future__ import annotations
import asyncio, os
from pathlib import Path
from opencode.util import log as logmod
from opencode.util.paths import GlobalPaths
from opencode.util.hash import fast as hash_fast

logger = logmod.create(service="snapshot")


async def _git(args: list[str], cwd: str | None = None, env: dict | None = None) -> tuple[int, str, str]:
    merged_env = {**os.environ, **(env or {})}
    proc = await asyncio.create_subprocess_exec(
        "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=cwd, env=merged_env,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class Snapshot:
    def __init__(self, project_id: str, worktree: str):
        self.worktree = worktree
        self.gitdir = str(GlobalPaths.data() / "snapshot" / project_id / hash_fast(worktree))
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return
        if not Path(self.gitdir).exists():
            Path(self.gitdir).mkdir(parents=True, exist_ok=True)
            await _git(["init"], env={"GIT_DIR": self.gitdir, "GIT_WORK_TREE": self.worktree})
            for k, v in [("core.autocrlf", "false"), ("core.longpaths", "true"), ("core.fsmonitor", "false")]:
                await _git(["--git-dir", self.gitdir, "config", k, v])
        self._initialized = True

    async def track(self) -> str | None:
        await self.init()
        await _git(["--git-dir", self.gitdir, "--work-tree", self.worktree, "add", "--sparse", "."],
                    cwd=self.worktree)
        code, text, _ = await _git(["--git-dir", self.gitdir, "--work-tree", self.worktree, "write-tree"],
                                    cwd=self.worktree)
        if code != 0:
            return None
        h = text.strip()
        logger.debug("tracked", hash=h)
        return h

    async def diff(self, hash: str) -> str:
        await self.init()
        await _git(["--git-dir", self.gitdir, "--work-tree", self.worktree, "add", "--sparse", "."],
                    cwd=self.worktree)
        code, text, _ = await _git(
            ["--git-dir", self.gitdir, "--work-tree", self.worktree, "diff", "--cached", "--no-ext-diff", hash, "--", "."],
            cwd=self.worktree)
        return text.strip() if code == 0 else ""

    async def patch(self, hash: str) -> dict:
        await self.init()
        await _git(["--git-dir", self.gitdir, "--work-tree", self.worktree, "add", "--sparse", "."],
                    cwd=self.worktree)
        code, text, _ = await _git(
            ["--git-dir", self.gitdir, "--work-tree", self.worktree, "diff", "--cached", "--no-ext-diff", "--name-only", hash, "--", "."],
            cwd=self.worktree)
        files = [os.path.join(self.worktree, f.strip()) for f in text.strip().split("\n") if f.strip()] if code == 0 else []
        return {"hash": hash, "files": files}

    async def restore(self, snapshot_hash: str) -> None:
        code, _, err = await _git(
            ["--git-dir", self.gitdir, "--work-tree", self.worktree, "read-tree", snapshot_hash], cwd=self.worktree)
        if code == 0:
            await _git(["--git-dir", self.gitdir, "--work-tree", self.worktree, "checkout-index", "-a", "-f"],
                        cwd=self.worktree)
        else:
            logger.error("restore failed", hash=snapshot_hash, error=err)
