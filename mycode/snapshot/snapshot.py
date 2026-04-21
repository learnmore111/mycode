from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from mycode.util import log as logmod
from mycode.util.git_async import git as _git
from mycode.util.git_async import git_available as _git_available
from mycode.util.hash import fast as hash_fast
from mycode.util.paths import GlobalPaths

logger = logmod.create(service="snapshot")


@dataclass
class SnapshotEntry:
    """A snapshot history entry."""
    tree_hash: str
    commit_hash: str
    message: str
    timestamp: int


class Snapshot:
    def __init__(self, project_id: str, worktree: str):
        self.worktree = worktree
        self.gitdir = str(GlobalPaths.data() / "snapshot" / project_id / hash_fast(worktree))
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return
        if not _git_available():
            logger.warn("git not available, snapshots disabled")
            return
        gitdir = Path(self.gitdir)
        gitdir.mkdir(parents=True, exist_ok=True)
        # Check if git repo already initialized (handles race with exist_ok)
        if not (gitdir / "HEAD").exists():
            await _git(["init"], env={"GIT_DIR": self.gitdir, "GIT_WORK_TREE": self.worktree})
            for k, v in [
                ("core.autocrlf", "false"),
                ("core.longpaths", "true"),
                ("core.fsmonitor", "false"),
                ("user.email", "mycode@snapshot"),
                ("user.name", "mycode"),
            ]:
                await _git(["--git-dir", self.gitdir, "config", k, v])
        self._initialized = True

    def _base_args(self) -> list[str]:
        return ["--git-dir", self.gitdir, "--work-tree", self.worktree]

    async def track(self, message: str = "snapshot") -> str | None:
        """Stage all files, write tree, and create a commit. Returns the commit hash."""
        await self.init()
        if not self._initialized:
            return None
        # Check git add success before proceeding
        add_code, _, add_err = await _git([*self._base_args(), "add", "--sparse", "."], cwd=self.worktree)
        if add_code != 0:
            logger.warn("git add failed", error=add_err.strip())
        code, tree_text, _ = await _git([*self._base_args(), "write-tree"], cwd=self.worktree)
        if code != 0:
            return None
        tree_hash = tree_text.strip()

        # Create a commit for history tracking
        code, commit_text, _ = await _git(
            [*self._base_args(), "commit-tree", tree_hash, "-m", message],
            cwd=self.worktree,
        )
        if code == 0:
            commit_hash = commit_text.strip()
            # Update a ref so we can list history
            await _git([*self._base_args(), "update-ref", "refs/heads/snapshots", commit_hash])
            logger.debug("tracked", tree=tree_hash, commit=commit_hash)
            return tree_hash

        logger.debug("tracked (no commit)", tree=tree_hash)
        return tree_hash

    async def diff(self, tree_hash: str) -> str:
        await self.init()
        if not self._initialized:
            return ""
        await _git([*self._base_args(), "add", "--sparse", "."], cwd=self.worktree)
        code, text, _ = await _git(
            [*self._base_args(), "diff", "--cached", "--no-ext-diff", tree_hash, "--", "."],
            cwd=self.worktree,
        )
        return text.strip() if code == 0 else ""

    async def patch(self, tree_hash: str) -> dict:
        await self.init()
        if not self._initialized:
            return {"hash": tree_hash, "files": []}
        await _git([*self._base_args(), "add", "--sparse", "."], cwd=self.worktree)
        code, text, _ = await _git(
            [*self._base_args(), "diff", "--cached", "--no-ext-diff", "--name-only", tree_hash, "--", "."],
            cwd=self.worktree,
        )
        files = (
            [os.path.join(self.worktree, f.strip()) for f in text.strip().split("\n") if f.strip()]
            if code == 0
            else []
        )
        return {"hash": tree_hash, "files": files}

    async def restore(self, snapshot_hash: str) -> bool:
        """Restore worktree to the given snapshot. Returns True on success."""
        await self.init()
        if not self._initialized:
            return False
        code, _, err = await _git(
            [*self._base_args(), "read-tree", snapshot_hash], cwd=self.worktree,
        )
        if code == 0:
            # Remove untracked files first
            await _git([*self._base_args(), "clean", "-fd"], cwd=self.worktree)
            await _git(
                [*self._base_args(), "checkout-index", "-a", "-f"],
                cwd=self.worktree,
            )
            return True
        logger.error("restore failed", hash=snapshot_hash, error=err)
        return False

    async def list_history(self, limit: int = 20) -> list[SnapshotEntry]:
        """List recent snapshot commits."""
        await self.init()
        if not self._initialized:
            return []
        code, text, _ = await _git(
            [*self._base_args(), "log", "--format=%H %T %s %ct", f"-{limit}", "refs/heads/snapshots"],
            cwd=self.worktree,
        )
        if code != 0 or not text.strip():
            return []
        entries = []
        for line in text.strip().split("\n"):
            parts = line.split(" ", 3)
            if len(parts) >= 4:
                entries.append(SnapshotEntry(
                    commit_hash=parts[0],
                    tree_hash=parts[1],
                    message=parts[2] if len(parts) > 3 else "",
                    timestamp=int(parts[3]) if parts[3].isdigit() else 0,
                ))
        return entries
