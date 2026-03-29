"""Bash tool — execute shell commands. Equivalent to src/tool/bash.ts."""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from opencode.project.instance import current_or_none
from opencode.tool.base import ToolContext, ToolInfo, ToolResult


class BashTool(ToolInfo):
    id = "bash"
    description = "Execute a shell command. Use this to run commands, install packages, or interact with the system."

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in milliseconds (default: 120000)"},
            },
            "required": ["command"],
        }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = args["command"]
        timeout = args.get("timeout", 120000) / 1000

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
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            code = proc.returncode or 0

            # Truncate very long output
            if len(output) > 100_000:
                output = output[:50_000] + f"\n\n... truncated ({len(output)} chars total) ...\n\n" + output[-50_000:]

            return ToolResult(
                title=command[:80],
                output=f"Exit code: {code}\n{output}" if code != 0 else output,
                metadata={"exit_code": code},
            )
        except TimeoutError:
            return ToolResult(title=command[:80], output="Command timed out.", metadata={"exit_code": -1, "timeout": True})
        except Exception as e:
            return ToolResult(title=command[:80], output=f"Error: {e}", metadata={"exit_code": -1})

tool = BashTool()
