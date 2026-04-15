"""Grep tool using ripgrep.

Features:
- ToolResultBuilder for output truncation control
- Context lines (-C 1) for better match understanding
- Structured match count and file count in metadata
- Clear truncation message when results exceed limits
- Binary file exclusion (--no-binary / -I)
- File size limit (--max-filesize 1M)
- Capability declarations (is_read_only=True)
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from pydantic import BaseModel, Field

from opencode.file.ignore import IGNORED_DIRS, RG_EXCLUDE_GLOBS
from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder

# Maximum matches to return
_MAX_MATCHES = 100


class GrepParams(BaseModel):
    """Parameters for the grep tool."""
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="Directory to search in (relative to project root or absolute)")
    include: str | None = Field(default=None, description="File glob filter (e.g. '*.py', '*.ts')")


class GrepTool(CallableTool[GrepParams]):
    id = "grep"
    description = "Search file contents using a regex pattern. Uses ripgrep. Returns matching lines with file paths and line numbers."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True

    async def call(self, params: GrepParams, ctx: ToolContext) -> ToolResult:
        pattern = params.pattern
        path = params.path
        include = params.include
        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()
        cwd = os.path.join(base, path) if not os.path.isabs(path) else path

        if not os.path.isdir(cwd):
            return ToolError(
                f"Search directory not found: {path}",
                title=f"Grep {pattern}",
            )

        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "-rn", "--no-heading", "-C", "1", "-m", str(_MAX_MATCHES),
                   "--no-binary",  # Skip binary files
                   "--max-filesize", "1M"]  # Skip files larger than 1MB
            for glob_pattern in RG_EXCLUDE_GLOBS:
                cmd += ["--glob", glob_pattern]
            if include:
                cmd += ["-g", include]
            cmd.append(pattern)
            cmd.append(".")
            exec_cwd = cwd
        else:
            cmd = ["grep", "-rn", "-I",  # -I: skip binary files
                   "-m", str(_MAX_MATCHES)]
            for d in sorted(IGNORED_DIRS):
                if "*" not in d:
                    cmd += ["--exclude-dir", d]
            cmd.append(pattern)
            cmd.append(cwd)
            exec_cwd = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=exec_cwd)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace").strip()

            if not output:
                return ToolOk(
                    f"No matches found for pattern: {pattern}",
                    title=f"Grep {pattern}",
                    metadata={"matches": 0, "files": 0, "pattern": pattern},
                )

            # Count unique files and match lines
            match_lines = [line for line in output.split("\n") if line and not line.startswith("--")]
            match_count = len(match_lines)
            unique_files: set[str] = set()
            for line in match_lines:
                # ripgrep format: file:line:content
                parts = line.split(":", 2)
                if len(parts) >= 2:
                    unique_files.add(parts[0])
            file_count = len(unique_files)

            builder = ToolResultBuilder(max_chars=50_000)
            builder.add(f"Found {match_count} match(es) in {file_count} file(s) for pattern: {pattern}\n\n")
            builder.add(output)

            if match_count >= _MAX_MATCHES:
                builder.add(f"\n\n... results limited to {_MAX_MATCHES} matches. "
                            "Narrow your search with a more specific pattern or use the include parameter.")

            return ToolOk(
                builder.build(),
                title=f"Grep {pattern}",
                metadata={
                    "matches": match_count,
                    "files": file_count,
                    "pattern": pattern,
                    "truncated": match_count >= _MAX_MATCHES or builder.truncated,
                },
            )
        except TimeoutError:
            return ToolError(
                "Search timed out after 30s. Try a more specific pattern or narrower path.",
                title=f"Grep {pattern}",
            )
        except Exception as e:
            return ToolError(f"Error: {e}", title=f"Grep {pattern}")


tool = GrepTool()
