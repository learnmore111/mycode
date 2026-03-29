"""System prompt assembly. Equivalent to src/session/system.ts."""
from __future__ import annotations
import os, platform, time
from opencode.project.instance import current_or_none

def build(*, agent_prompt: str | None = None, instructions: list[str] | None = None) -> list[str]:
    """Build the system prompt list for an LLM call."""
    parts: list[str] = []
    ctx = current_or_none()
    cwd = ctx.directory if ctx else os.getcwd()
    worktree = ctx.worktree if ctx else cwd

    base = f"""You are an AI coding assistant. You help users with programming tasks.

Current working directory: {cwd}
Project root: {worktree}
Platform: {platform.system()} {platform.machine()}
Date: {time.strftime("%Y-%m-%d")}

You have access to tools that let you read files, write files, execute commands, and search codebases.
Always prefer using tools over guessing. Be concise and helpful."""
    parts.append(base)

    if agent_prompt:
        parts.append(agent_prompt)
    if instructions:
        for inst in instructions:
            parts.append(inst)
    return parts
