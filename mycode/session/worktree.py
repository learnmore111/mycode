"""Git 工作树生命周期管理，用于隔离的子代理执行。

为隔离子代理模式使用的临时 git 工作树提供创建/差异/应用/清理操作。
"""
from __future__ import annotations

import asyncio
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
    """已创建工作树的信息。"""
    path: str          # 工作树目录的绝对路径
    branch: str        # 为此工作树创建的分支名称
    base_commit: str   # 此工作树基于的分支提交
    task_id: str       # 此工作树的唯一标识符
    project_dir: str   # 原始项目目录（避免脆弱的路径计算）


async def create_worktree(project_dir: str, task_id: str | None = None) -> WorktreeInfo | None:
    """为隔离的子代理执行创建临时 git 工作树。

    参数:
        project_dir: 主项目目录（必须是 git 仓库）。
        task_id: 可选的唯一标识符。如果未提供则自动生成。

    返回:
        成功时返回 WorktreeInfo，如果 git 不可用或不是 git 仓库则返回 None。
    """
    if not git_available():
        logger.warn("git not available, cannot create worktree")
        return None

    # 验证这是一个 git 仓库
    code, _, _ = await git(["rev-parse", "--is-inside-work-tree"], cwd=project_dir)
    if code != 0:
        logger.warn("not a git repo, cannot create worktree", path=project_dir)
        return None

    # 获取当前 HEAD 提交
    code, head_out, _ = await git(["rev-parse", "HEAD"], cwd=project_dir)
    if code != 0:
        # 尚无提交 — 使用空提交初始化
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
    """暂存所有更改并一次性收集差异和变更文件列表。

    返回:
        (diff_text, changed_file_list) — 避免重复运行 `git add -A`。
    """
    code, _, _ = await git(["add", "-A"], cwd=worktree.path)
    if code != 0:
        return "", []

    # 获取 diff
    code, diff_out, _ = await git(
        ["diff", "--cached", "--no-ext-diff", worktree.base_commit],
        cwd=worktree.path,
    )
    diff = diff_out.strip() if code == 0 else ""

    # 获取变更的文件名
    code, names_out, _ = await git(
        ["diff", "--cached", "--name-only", worktree.base_commit],
        cwd=worktree.path,
    )
    changed = [f.strip() for f in names_out.strip().split("\n") if f.strip()] if code == 0 and names_out.strip() else []

    return diff, changed


async def apply_diff_text(diff: str, target_dir: str) -> bool:
    """将差异字符串应用到目标目录。

    直接接受差异文本（调用方已经拥有），以避免冗余的 git 操作。

    返回:
        如果补丁应用成功则返回 True，否则返回 False。
    """
    if not diff:
        return True

    # 首先尝试使用 --3way 以获得更好的合并处理
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
        # 不使用 --3way 的降级方案
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
    """移除工作树及其关联分支。

    即使工作树已被移除或 git 处于异常状态，也可以安全调用。从不抛出异常 —
    每一步都是尽力而为，因为此函数运行在 `finally` 块中，抛出异常会掩盖
    调用方的真正错误（通常是隔离子代理运行）。
    """
    project_dir = worktree.project_dir
    path = worktree.path
    branch = worktree.branch

    # 步骤 1 — 要求 git 分离并移除工作树元数据。
    try:
        code, _, err = await git(["worktree", "remove", "--force", path], cwd=project_dir)
        if code != 0:
            logger.debug("worktree remove returned non-zero", path=path, error=err)
    except Exception as e:
        logger.warn("worktree remove raised, will fall back to rmtree", path=path, error=str(e))

    # 步骤 2 — 目录可能仍然存在（尤其是在 `remove` 失败后）。
    # 使用 `ignore_errors` 强制删除，以便卡住文件（打开编辑器等）
    # 不会向上泄漏此错误。在短暂退避后重试第二次，
    # 因为 macOS fsevents 有时会短暂持有句柄。
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)
        if os.path.exists(path):
            await asyncio.sleep(0.2)
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                logger.warn("worktree directory still present after cleanup", path=path)

    # 步骤 3 — 尽力清理分支 / 修剪。绝不抛出异常。
    for args in (["branch", "-D", branch], ["worktree", "prune"]):
        try:
            await git(args, cwd=project_dir)
        except Exception as e:
            logger.debug("git cleanup step failed, continuing", args=args, error=str(e))

    logger.info("worktree cleaned up", task_id=worktree.task_id, branch=branch)
