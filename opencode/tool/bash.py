"""Bash tool — execute shell commands. Equivalent to src/tool/bash.ts."""
from __future__ import annotations

import asyncio
import os
import shutil

from pydantic import BaseModel, Field

from opencode.project.instance import current_or_none
from opencode.tool.base import CallableTool, ToolContext, ToolError, ToolOk, ToolResult, ToolResultBuilder


class BashParams(BaseModel):
    """Parameters for the bash tool."""
    command: str = Field(description="The shell command to execute")
    timeout: int = Field(default=120000, description="Timeout in milliseconds (default: 120000)")


class BashTool(CallableTool[BashParams]):
    id = "bash"
    description = "Execute a shell command. Use this to run commands, install packages, or interact with the system."

    async def call(self, params: BashParams, ctx: ToolContext) -> ToolResult:
        command = params.command
        timeout_sec = params.timeout / 1000

        inst = current_or_none()
        cwd = inst.directory if inst else os.getcwd()

        # Find shell
        shell = os.environ.get("SHELL", "/bin/sh")
        if os.path.basename(shell) in ("fish", "nu"):
            shell = shutil.which("bash") or shutil.which("zsh") or "/bin/sh"

        try:
            proc = await asyncio.create_subprocess_exec(
                shell, "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env={**os.environ, "AGENT": "1"},
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            code = proc.returncode or 0

            # Use ToolResultBuilder for output truncation
            builder = ToolResultBuilder(max_chars=100_000)
            if code != 0:
                builder.add(f"Exit code: {code}\n")
            builder.add(output)

            return ToolOk(
                builder.build(),
                title=command[:80],
                metadata={"exit_code": code},
            )
        except TimeoutError:
            return ToolError(
                "Command timed out.",
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
