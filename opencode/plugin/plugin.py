from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from opencode.util import log as logmod

logger = logmod.create(service="plugin")

Hook = Callable[..., Any]
_HOOK_TIMEOUT = 10.0  # seconds — max time for a single hook execution


class HookType(StrEnum):
    """Well-known hook points in the system."""

    BEFORE_TOOL = "before_tool"  # (tool_name, args) -> modified args or None
    AFTER_TOOL = "after_tool"  # (tool_name, result) -> modified result or None
    BEFORE_PROMPT = "before_prompt"  # (prompt_input,) -> modified input or None
    AFTER_PROMPT = "after_prompt"  # (prompt_event,) -> modified event or None
    BEFORE_LLM = "before_llm"  # (stream_input,) -> modified input or None
    SYSTEM_PROMPT = "system_prompt"  # (parts: list[str],) -> modified parts or None
    ON_ERROR = "on_error"  # (error,) -> None


@dataclass
class PluginInfo:
    name: str
    status: str  # "loaded" | "failed"
    error: str | None = None


class PluginManager:
    """Manages loaded plugins and their hooks."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {}
        self._plugins: list[PluginInfo] = []

    async def init(self, plugin_specs: list[Any] | None = None) -> None:
        if not plugin_specs:
            return
        for spec in plugin_specs:
            name = spec if isinstance(spec, str) else spec[0]
            config = spec[1] if isinstance(spec, list) and len(spec) > 1 else {}
            try:
                mod = importlib.import_module(name)
                if hasattr(mod, "server"):
                    hooks = await mod.server(config)
                    self._register_hooks(hooks)
                    self._plugins.append(PluginInfo(name=name, status="loaded"))
                    logger.info("loaded plugin", name=name)
                else:
                    logger.warn("plugin has no server export", name=name)
            except Exception as e:
                logger.warn("failed to load plugin", name=name, error=str(e))
                self._plugins.append(PluginInfo(name=name, status="failed", error=str(e)))

    def _register_hooks(self, hooks: dict[str, Any]) -> None:
        for name, fn in hooks.items():
            if callable(fn):
                self._hooks.setdefault(name, []).append(fn)

    def register_hook(self, hook_name: str, fn: Hook) -> Callable[[], None]:
        """Register a single hook. Returns an unregister function."""
        hooks = self._hooks.setdefault(hook_name, [])
        hooks.append(fn)

        def unregister() -> None:
            hooks.remove(fn)

        return unregister

    async def trigger(self, hook_name: str, hook_input: Any, output: Any = None) -> Any:
        """Trigger a hook with chain-style passing.

        Each hook receives the current output and can return a modified version.
        If a hook returns None, the previous output is preserved.
        Hooks are given a timeout to prevent blocking.
        """
        current = output
        for fn in list(self._hooks.get(hook_name, [])):  # Copy list to avoid mutation during iteration
            try:
                result = await asyncio.wait_for(fn(hook_input, current), timeout=_HOOK_TIMEOUT)
                if result is not None:
                    current = result
            except TimeoutError:
                logger.error("hook timed out", hook=hook_name, timeout=_HOOK_TIMEOUT)
            except Exception as e:
                logger.error("hook failed", hook=hook_name, error=str(e))
        return current

    def unload(self, plugin_name: str) -> bool:
        """Unload a plugin and remove all its hooks."""
        found = False
        self._plugins = [p for p in self._plugins if p.name != plugin_name or not (found := True)]  # noqa: F841
        if found:
            logger.info("unloaded plugin", name=plugin_name)
        return found

    def list_plugins(self) -> list[dict[str, Any]]:
        return [{"name": p.name, "status": p.status, "error": p.error} for p in self._plugins]

    def list_hooks(self) -> dict[str, int]:
        """List all registered hooks and their handler count."""
        return {name: len(fns) for name, fns in self._hooks.items() if fns}
