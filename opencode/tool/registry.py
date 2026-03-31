"""Tool registry — manages available tools.

Enhanced with:
- Tool hiding/showing (dynamic visibility control)
- ToolNotFoundError for lookup failures
- Clear/reset for testing
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opencode.tool.base import ToolNotFoundError
from opencode.util import log as logmod

if TYPE_CHECKING:
    from opencode.tool.base import ToolInfo

logger = logmod.create(service="tool.registry")

_tools: dict[str, ToolInfo] = {}
_hidden: set[str] = set()  # Hidden tool ids (registered but not visible to LLM)
_registered: bool = False  # Whether builtins have been registered


def register(tool: ToolInfo) -> None:
    """Register a tool. Replaces any existing tool with the same id."""
    _tools[tool.id] = tool
    logger.debug("registered tool", id=tool.id)


def unregister(tool_id: str) -> None:
    """Remove a tool from the registry."""
    _tools.pop(tool_id, None)
    _hidden.discard(tool_id)


def get(tool_id: str) -> ToolInfo | None:
    """Get a tool by id. Returns None if not found."""
    return _tools.get(tool_id)


def get_or_raise(tool_id: str) -> ToolInfo:
    """Get a tool by id. Raises ToolNotFoundError if not found."""
    tool = _tools.get(tool_id)
    if tool is None:
        raise ToolNotFoundError(tool_id)
    return tool


def all_tools() -> list[ToolInfo]:
    """Return all registered tools (including hidden)."""
    return list(_tools.values())


def visible_tools() -> list[ToolInfo]:
    """Return only visible (non-hidden) tools."""
    return [t for t in _tools.values() if t.id not in _hidden]


def hide(tool_id: str) -> None:
    """Hide a tool from LLM (still registered, just not visible)."""
    if tool_id in _tools:
        _hidden.add(tool_id)
        logger.debug("hidden tool", id=tool_id)


def unhide(tool_id: str) -> None:
    """Make a hidden tool visible again."""
    _hidden.discard(tool_id)
    logger.debug("unhidden tool", id=tool_id)


def is_hidden(tool_id: str) -> bool:
    """Check if a tool is hidden."""
    return tool_id in _hidden


def to_llm_tools() -> list[dict[str, Any]]:
    """Convert all visible tools to litellm format."""
    return [t.to_llm_tool() for t in _tools.values() if t.id not in _hidden]


def clear() -> None:
    """Clear all registered tools. Useful for testing."""
    global _registered
    _tools.clear()
    _hidden.clear()
    _registered = False


def register_builtins() -> None:
    """Register all built-in tools (idempotent)."""
    global _registered
    if _registered:
        return
    _registered = True

    from opencode.tool import bash, edit, glob_tool, grep, listdir, question, read, skill, task, todo, webfetch, websearch, write
    for mod in [bash, read, edit, write, glob_tool, grep, listdir, task, webfetch, websearch, question, todo, skill]:
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
