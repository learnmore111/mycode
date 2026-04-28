"""List directory tool — explore project structure."""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from mycode.file.ignore import should_ignore_entry
from mycode.project.instance import current_or_none
from mycode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, validate_path_safety


class ListDirParams(BaseModel):
    """Parameters for the listdir tool."""
    path: str = Field(default="", description="Directory path to list (relative to project root, or absolute). Empty means project root.")
    recursive: bool = Field(default=False, description="If true, list recursively (tree view). Depth limited to 3 levels.")


class ListDirTool(CallableTool[ListDirParams]):
    id = "listdir"
    description = (
        "List the contents of a directory. Shows files and subdirectories with type indicators. "
        "Use recursive=true for a tree view (limited to 3 levels deep)."
    )

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: ListDirParams, ctx: ToolContext) -> ToolResult:
        dir_path = params.path
        recursive = params.recursive

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        resolved = os.path.join(base, dir_path) if dir_path and not os.path.isabs(dir_path) else (dir_path or base)

        # Path safety validation — prevent directory traversal
        if dir_path:
            path_error = validate_path_safety(resolved, base)
            if path_error:
                return ToolError(f"Path not allowed: {path_error}", title=f"List {dir_path or '.'}")

        if not os.path.isdir(resolved):
            return ToolError(f"Not a directory: {dir_path or '.'}", title=f"List {dir_path or '.'}")

        try:
            lines = _tree(resolved, base, max_depth=3) if recursive else _flat(resolved, base)

            if not lines:
                return ToolOk("(empty directory)", title=f"List {dir_path or '.'}", metadata={"count": 0})

            if len(lines) > 500:
                output = "\n".join(lines[:500]) + f"\n\n... truncated ({len(lines)} total entries)"
            else:
                output = "\n".join(lines)

            return ToolOk(output, title=f"List {dir_path or '.'}", metadata={"count": len(lines)})
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"List {dir_path or '.'}")


def _flat(directory: str, base: str) -> list[str]:
    """List a single directory level."""
    lines: list[str] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return ["(permission denied)"]
    for entry in entries:
        if should_ignore_entry(entry.name):
            continue
        rel = os.path.relpath(entry.path, base)
        if entry.is_dir():
            lines.append(f"[dir]  {rel}/")
        else:
            try:
                size = entry.stat().st_size
                lines.append(f"[file] {rel}  ({_human_size(size)})")
            except OSError:
                lines.append(f"[file] {rel}  (unreadable)")
    return lines


def _tree(directory: str, base: str, max_depth: int = 3, _depth: int = 0, _prefix: str = "") -> list[str]:
    """Recursively list directory as a tree."""
    if _depth > max_depth:
        return [f"{_prefix}..."]
    lines: list[str] = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return [f"{_prefix}(permission denied)"]
    filtered = [e for e in entries if not should_ignore_entry(e.name)]
    for i, entry in enumerate(filtered):
        is_last = i == len(filtered) - 1
        connector = "└── " if is_last else "├── "
        if entry.is_dir():
            lines.append(f"{_prefix}{connector}{entry.name}/")
            extension = "    " if is_last else "│   "
            try:
                lines.extend(_tree(entry.path, base, max_depth, _depth + 1, _prefix + extension))
            except OSError:
                lines.append(f"{_prefix}{extension}(inaccessible)")
        else:
            lines.append(f"{_prefix}{connector}{entry.name}")
    return lines


def _human_size(size: int) -> str:
    """Convert bytes to human-readable size."""
    value: float = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


tool = ListDirTool()
