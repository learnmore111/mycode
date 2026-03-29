"""Tool registry — manages available tools. Equivalent to src/tool/registry.ts."""
from __future__ import annotations
from typing import Any
from opencode.tool.base import ToolInfo
from opencode.util import log as logmod

logger = logmod.create(service="tool.registry")

_tools: dict[str, ToolInfo] = {}

def register(tool: ToolInfo) -> None:
    _tools[tool.id] = tool
    logger.debug("registered tool", id=tool.id)

def get(tool_id: str) -> ToolInfo | None:
    return _tools.get(tool_id)

def all_tools() -> list[ToolInfo]:
    return list(_tools.values())

def to_llm_tools() -> list[dict[str, Any]]:
    """Convert all registered tools to litellm format."""
    return [t.to_llm_tool() for t in _tools.values()]

def register_builtins() -> None:
    """Register all built-in tools."""
    from opencode.tool import bash, read, edit, write, glob_tool, grep, task, webfetch, websearch, question, todo, skill
    for mod in [bash, read, edit, write, glob_tool, grep, task, webfetch, websearch, question, todo, skill]:
        if hasattr(mod, "tool"):
            register(mod.tool)
