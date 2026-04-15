"""Bash tool — execute shell commands.

Features:
- Separate stderr capture (distinct from stdout in output)
- Custom environment variable passing
- Working directory safety validation (prevent directory escape)
- Capability declarations (is_read_only, is_destructive, is_concurrency_safe)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
from typing import Any

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import (
    CallableTool,
    ToolContext,
    ToolError,
    ToolOk,
    ToolResult,
    ToolResultBuilder,
    validate_path_safety,
)


class BashParams(BaseModel):
    """Parameters for the bash tool."""
    command: str = Field(description="The shell command to execute")
    timeout: int = Field(default=120000, description="Timeout in milliseconds (default: 120000)")
    cwd: str | None = Field(default=None, description="Working directory for the command (default: project root)")
    env: dict[str, str] | None = Field(default=None, description="Additional environment variables to set")


class BashTool(CallableTool[BashParams]):
    id = "bash"
    description = "Execute a shell command. Use this to run commands, install packages, or interact with the system."

    def is_read_only(self, args: dict[str, Any] | None = None) -> bool:
        return False  # Cannot determine statically; assume write

    def is_destructive(self, args: dict[str, Any] | None = None) -> bool:
        return False  # Would need command semantic analysis

    def is_concurrency_safe(self, args: dict[str, Any] | None = None) -> bool:
        return True  # Shell commands are isolated processes

    async def call(self, params: BashParams, ctx: ToolContext) -> ToolResult:
        command = params.command
        timeout_sec = params.timeout / 1000

        inst = current_or_none()
        base = inst.directory if inst else os.getcwd()

        # Resolve working directory
        cwd = (os.path.join(base, params.cwd) if not os.path.isabs(params.cwd) else params.cwd) if params.cwd else base

        if not os.path.isdir(cwd):
            return ToolError(f"Working directory not found: {cwd}", title=command[:80])

        # Validate cwd stays within project (or its parent for common cases)
        cwd_error = validate_path_safety(cwd, base)
        if cwd_error and params.cwd:
            return ToolError(
                f"Working directory not allowed: {cwd_error}",
                title=command[:80],
            )

        # Find shell
        shell = os.environ.get("SHELL", "/bin/sh")
        if os.path.basename(shell) in ("fish", "nu"):
            shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"

        # Build environment
        env = {**os.environ, "AGENT": "1"}
        if params.env:
            env.update(params.env)

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                shell, "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            code = proc.returncode or 0

            builder = ToolResultBuilder(max_chars=100_000)
            if code != 0:
                builder.add(f"Exit code: {code}\n")
            if stdout:
                builder.add(stdout)
            if stderr:
                builder.add(f"\n--- stderr ---\n{stderr}")

            return ToolOk(
                builder.build(),
                title=command[:80],
                metadata={"exit_code": code, "has_stderr": bool(stderr)},
            )
        except TimeoutError:
            if proc and proc.returncode is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    await asyncio.sleep(0.5)
                    if proc.returncode is None:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        await proc.wait()  # Reap the zombie process
                except (ProcessLookupError, PermissionError):
                    pass
            return ToolError(
                f"Command timed out after {timeout_sec}s and was killed.",
                title=command[:80],
                metadata={"exit_code": -1, "timeout": True},
            )
        except Exception as e:
            return ToolError(
                f"Error: {e}",
                title=command[:80],
                metadata={"exit_code": -1},
            )


tool = BashTool()
