"""System prompt assembly.

Selects model-specific system prompts and builds environment info.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING

from mycode.project.instance import current_or_none

if TYPE_CHECKING:
    from mycode.provider.schema import Model

PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    p = PROMPTS_DIR / f"{name}.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


# Load all system prompts at import time
PROMPT_ANTHROPIC = _load_prompt("anthropic")
PROMPT_BEAST = _load_prompt("beast")
PROMPT_CODEX = _load_prompt("codex")
PROMPT_DEFAULT = _load_prompt("default")
PROMPT_GEMINI = _load_prompt("gemini")
PROMPT_GPT = _load_prompt("gpt")
PROMPT_TRINITY = _load_prompt("trinity")
PROMPT_PLAN = _load_prompt("plan")
PROMPT_BUILD_SWITCH = _load_prompt("build-switch")
PROMPT_MAX_STEPS = _load_prompt("max-steps")


def provider_prompt(model: Model) -> list[str]:
    """Select model-specific system prompt based on the model API ID."""
    api_id = model.api.id.lower()
    if "gpt-4" in api_id or "o1" in api_id or "o3" in api_id:
        return [PROMPT_BEAST] if PROMPT_BEAST else [PROMPT_DEFAULT]
    if "gpt" in api_id:
        if "codex" in api_id:
            return [PROMPT_CODEX] if PROMPT_CODEX else [PROMPT_DEFAULT]
        return [PROMPT_GPT] if PROMPT_GPT else [PROMPT_DEFAULT]
    if "gemini-" in api_id:
        return [PROMPT_GEMINI] if PROMPT_GEMINI else [PROMPT_DEFAULT]
    if "claude" in api_id:
        return [PROMPT_ANTHROPIC] if PROMPT_ANTHROPIC else [PROMPT_DEFAULT]
    if "trinity" in api_id:
        return [PROMPT_TRINITY] if PROMPT_TRINITY else [PROMPT_DEFAULT]
    return [PROMPT_DEFAULT] if PROMPT_DEFAULT else []


def environment(model: Model) -> list[str]:
    """Build environment info system prompt."""
    ctx = current_or_none()
    cwd = ctx.directory if ctx else os.getcwd()
    worktree = ctx.worktree if ctx else cwd

    # Check if git repo
    is_git = (Path(worktree) / ".git").exists()

    return [
        "\n".join([
            f"You are powered by the model named {model.api.id}. "
            f"The exact model ID is {model.provider_id}/{model.api.id}",
            "Here is some useful information about the environment you are running in:",
            "<env>",
            f"  Working directory: {cwd}",
            f"  Workspace root folder: {worktree}",
            f"  Is directory a git repo: {'yes' if is_git else 'no'}",
            f"  Platform: {platform.system()}",
            "</env>",
        ]),
    ]


def build(
    *,
    model: Model | None = None,
    agent_prompt: str | None = None,
    instructions: list[str] | None = None,
) -> list[str]:
    """Build the complete system prompt list for an LLM call."""
    parts: list[str] = []

    # Model-specific prompt
    if model:
        parts.extend(provider_prompt(model))
        parts.extend(environment(model))
    else:
        # Fallback if no model specified
        ctx = current_or_none()
        cwd = ctx.directory if ctx else os.getcwd()
        worktree = ctx.worktree if ctx else cwd
        parts.append(
            f"You are an AI coding assistant.\n"
            f"Working directory: {cwd}\nProject root: {worktree}\n"
            f"Platform: {platform.system()}"
        )

    if agent_prompt:
        parts.append(agent_prompt)
    if instructions:
        parts.extend(instructions)
    return parts
