"""Shared helpers for sub-agent execution (used by task.py and subagent.py).

Centralizes common functions to avoid duplication between the basic task tool
and the enhanced subagent tool.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mycode.permission.evaluate import evaluate as eval_permission
from mycode.permission.schema import Rule

if TYPE_CHECKING:
    from mycode.tool.base import ToolContext


def is_aborted(ctx: ToolContext) -> bool:
    """Check if the abort signal has been set."""
    abort = ctx.abort
    if abort is None:
        return False
    if isinstance(abort, asyncio.Event):
        return abort.is_set()
    if callable(abort):
        return bool(abort())
    return False


def build_agent_ruleset(agent: Any) -> list[Rule]:
    """Convert agent permission config dicts to Rule objects."""
    return [
        Rule(
            permission=r.get("permission", "*"),
            pattern=r.get("pattern", "*"),
            action=r.get("action", "ask"),
        )
        for r in (agent.permission or [])
    ]


def check_tool_permission(tool_name: str, ruleset: list[Rule]) -> str | None:
    """Check if a tool is allowed by the agent's permission ruleset.

    Returns None if allowed, or an error message string if denied.
    Permission rules that resolve to "ask" are treated as denied for sub-agents
    since there is no interactive user to ask.
    """
    result = eval_permission(tool_name, "*", ruleset)
    if result.action == "allow":
        return None
    if result.action == "deny":
        return f"Tool '{tool_name}' is denied by agent permission rules"
    return f"Tool '{tool_name}' requires interactive permission (not available in sub-agent)"
