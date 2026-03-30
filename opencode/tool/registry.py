"""Tool registry — manages available tools. Equivalent to src/tool/registry.ts."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opencode.util import log as logmod

if TYPE_CHECKING:
    from opencode.tool.base import ToolInfo

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
    from opencode.tool import bash, edit, glob_tool, grep, question, read, skill, task, todo, webfetch, websearch, write
    for mod in [bash, read, edit, write, glob_tool, grep, task, webfetch, websearch, question, todo, skill]:
        if hasattr(mod, "tool"):
            register(mod.tool)

    # Experimental: batch tool (explicit parallel execution)
    try:
        from opencode.config import config as configmod
        cfg = configmod.get()
        if cfg.experimental and cfg.experimental.batch_tool:
            from opencode.tool import batch
            if hasattr(batch, "tool"):
                register(batch.tool)
                logger.debug("batch tool enabled via experimental config")
    except Exception:
        pass  # Config not available yet, skip batch registration
