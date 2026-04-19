from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from mycode.project.instance import provide
from mycode.project.project import from_directory
from mycode.util import log as logmod

logger = logmod.create(service="routes.git")
router = APIRouter(prefix="/git", tags=["git"])

_MAX_DIFF_CHARS = 120_000
_CONFLICT_STATES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _empty_summary() -> dict[str, int]:
    return {
        "changed": 0,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "conflicted": 0,
        "modified": 0,
        "added": 0,
        "deleted": 0,
        "renamed": 0,
    }


async def _run_git(args: list[str], cwd: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "color.ui=false",
        "-c",
        "core.quotePath=false",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _is_within_worktree(worktree: str, rel_path: str) -> bool:
    try:
        root = Path(worktree).resolve()
        target = (root / rel_path).resolve()
        target.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


def _status_kind(index_status: str, worktree_status: str) -> str:
    pair = f"{index_status}{worktree_status}"
    if pair in _CONFLICT_STATES:
        return "conflicted"
    if pair == "??":
        return "untracked"
    if "R" in pair:
        return "renamed"
    if "D" in pair:
        return "deleted"
    if "A" in pair:
        return "added"
    if "C" in pair:
        return "added"
    return "modified"


def _parse_branch_line(line: str) -> dict[str, Any]:
    branch = line[3:].strip()
    upstream = None
    ahead = 0
    behind = 0

    tracking_match = re.match(r"^(?P<branch>.+?)(?:\.\.\.(?P<upstream>[^\[]+))?(?: \[(?P<tracking>.+)\])?$", branch)
    if tracking_match:
        branch = (tracking_match.group("branch") or branch).strip()
        upstream = tracking_match.group("upstream")
        tracking = tracking_match.group("tracking") or ""
        ahead_match = re.search(r"ahead (\d+)", tracking)
        behind_match = re.search(r"behind (\d+)", tracking)
        if ahead_match:
            ahead = int(ahead_match.group(1))
        if behind_match:
            behind = int(behind_match.group(1))

    return {
        "branch": None if branch.startswith("HEAD ") else branch,
        "upstream": upstream.strip() if upstream else None,
        "ahead": ahead,
        "behind": behind,
    }


def _parse_status_output(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = [line for line in text.splitlines() if line.strip()]
    branch_info: dict[str, Any] = {
        "branch": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
    }
    files: list[dict[str, Any]] = []

    for line in lines:
        if line.startswith("## "):
            branch_info = _parse_branch_line(line)
            continue
        if len(line) < 4:
            continue

        index_status = line[0]
        worktree_status = line[1]
        payload = line[3:]
        old_path = None
        path = payload
        if " -> " in payload:
            old_path, path = payload.split(" -> ", 1)

        kind = _status_kind(index_status, worktree_status)
        files.append({
            "path": path,
            "oldPath": old_path,
            "indexStatus": index_status,
            "worktreeStatus": worktree_status,
            "status": kind,
            "staged": index_status not in {" ", "?"},
            "unstaged": worktree_status not in {" ", "?"},
        })

    files.sort(key=lambda item: (item["status"], item["path"]))
    return branch_info, files


def _build_summary(files: list[dict[str, Any]]) -> dict[str, int]:
    summary = _empty_summary()
    summary["changed"] = len(files)
    for item in files:
        status = item["status"]
        if status in summary:
            summary[status] += 1
        if item["staged"]:
            summary["staged"] += 1
        if item["unstaged"]:
            summary["unstaged"] += 1
    return summary


async def _status_snapshot(worktree: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code, stdout, stderr = await _run_git(["status", "--short", "--branch", "--renames", "--untracked-files=all"], cwd=worktree)
    if code != 0:
        raise RuntimeError(stderr.strip() or "git status failed")
    return _parse_status_output(stdout)


async def _head_short(worktree: str) -> str | None:
    code, stdout, _stderr = await _run_git(["rev-parse", "--short", "HEAD"], cwd=worktree)
    return stdout.strip() if code == 0 and stdout.strip() else None


async def _has_head(worktree: str) -> bool:
    code, _stdout, _stderr = await _run_git(["rev-parse", "--verify", "HEAD"], cwd=worktree)
    return code == 0


async def _numstat_for_path(worktree: str, path: str, *, untracked: bool, has_head: bool) -> tuple[int, int, bool]:
    if untracked:
        code, stdout, _stderr = await _run_git(["diff", "--no-index", "--numstat", "--", "/dev/null", path], cwd=worktree)
        if code not in {0, 1}:
            return 0, 0, False
    else:
        args = ["diff", "--no-ext-diff", "--numstat"]
        if has_head:
            args.append("HEAD")
        args.extend(["--", path])
        code, stdout, _stderr = await _run_git(args, cwd=worktree)
        if code != 0:
            return 0, 0, False

    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        adds_raw, dels_raw = parts[0], parts[1]
        is_binary = adds_raw == "-" or dels_raw == "-"
        additions = 0 if is_binary else int(adds_raw or 0)
        deletions = 0 if is_binary else int(dels_raw or 0)
        return additions, deletions, is_binary
    return 0, 0, False


async def _diff_for_file(worktree: str, file_info: dict[str, Any]) -> tuple[str, int, int, bool, bool]:
    path = file_info["path"]
    untracked = file_info["status"] == "untracked"
    has_head = await _has_head(worktree)

    if untracked:
        code, stdout, stderr = await _run_git(["diff", "--no-index", "--", "/dev/null", path], cwd=worktree)
        if code not in {0, 1}:
            raise RuntimeError(stderr.strip() or f"git diff failed for {path}")
    else:
        args = ["diff", "--no-ext-diff", "--binary"]
        if has_head:
            args.append("HEAD")
        args.extend(["--", path])
        code, stdout, stderr = await _run_git(args, cwd=worktree)
        if code != 0:
            raise RuntimeError(stderr.strip() or f"git diff failed for {path}")

    additions, deletions, is_binary = await _numstat_for_path(worktree, path, untracked=untracked, has_head=has_head)
    too_large = len(stdout) > _MAX_DIFF_CHARS
    diff_text = stdout[:_MAX_DIFF_CHARS] if too_large else stdout
    return diff_text, additions, deletions, is_binary, too_large


@router.get("/status")
async def git_status(directory: str = Query(default=".")):
    project = await from_directory(directory)

    async def _fn():
        if project.vcs != "git":
            return {
                "available": False,
                "reason": "当前目录不是 Git 仓库",
                "worktree": project.worktree,
                "branch": None,
                "upstream": None,
                "head": None,
                "ahead": 0,
                "behind": 0,
                "clean": True,
                "summary": _empty_summary(),
                "files": [],
                "lastUpdated": int(time.time() * 1000),
            }

        try:
            branch_info, files = await _status_snapshot(project.worktree)
            head = await _head_short(project.worktree)
        except RuntimeError as exc:
            logger.warning("git status failed", error=str(exc), worktree=project.worktree)
            return {
                "available": False,
                "reason": str(exc),
                "worktree": project.worktree,
                "branch": None,
                "upstream": None,
                "head": None,
                "ahead": 0,
                "behind": 0,
                "clean": True,
                "summary": _empty_summary(),
                "files": [],
                "lastUpdated": int(time.time() * 1000),
            }

        return {
            "available": True,
            "reason": None,
            "worktree": project.worktree,
            "branch": branch_info["branch"],
            "upstream": branch_info["upstream"],
            "head": head,
            "ahead": branch_info["ahead"],
            "behind": branch_info["behind"],
            "clean": len(files) == 0,
            "summary": _build_summary(files),
            "files": files,
            "lastUpdated": int(time.time() * 1000),
        }

    return await provide(directory, _fn, project=project)


@router.get("/diff")
async def git_diff(path: str = Query(...), directory: str = Query(default=".")):
    project = await from_directory(directory)

    async def _fn():
        if project.vcs != "git":
            raise HTTPException(404, "当前目录不是 Git 仓库")
        if not _is_within_worktree(project.worktree, path):
            raise HTTPException(400, "文件路径超出 Git 工作区")

        try:
            branch_info, files = await _status_snapshot(project.worktree)
            head = await _head_short(project.worktree)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc

        file_info = next((item for item in files if item["path"] == path), None)
        if not file_info:
            raise HTTPException(404, f"未找到改动文件: {path}")

        try:
            diff_text, additions, deletions, is_binary, too_large = await _diff_for_file(project.worktree, file_info)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc

        return {
            "available": True,
            "path": file_info["path"],
            "oldPath": file_info.get("oldPath"),
            "status": file_info["status"],
            "staged": file_info["staged"],
            "unstaged": file_info["unstaged"],
            "branch": branch_info["branch"],
            "head": head,
            "tooLarge": too_large,
            "diff": diff_text,
            "stats": {
                "additions": additions,
                "deletions": deletions,
                "isBinary": is_binary,
            },
            "lastUpdated": int(time.time() * 1000),
        }

    return await provide(directory, _fn, project=project)
