from __future__ import annotations

import asyncio
import contextlib
import importlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mycode.util import log as logmod

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
        # Per-plugin hook registry so unload() can actually remove hooks
        # registered by a given plugin (previously unload was a no-op that
        # only dropped the PluginInfo record).
        self._plugin_hooks: dict[str, list[tuple[str, Hook]]] = {}
        # Remember original config so reload() can re-init without the
        # caller having to resupply it.
        self._plugin_configs: dict[str, Any] = {}

    async def init(self, plugin_specs: list[Any] | None = None) -> None:
        if not plugin_specs:
            return
        for spec in plugin_specs:
            name = spec if isinstance(spec, str) else spec[0]
            config = spec[1] if isinstance(spec, list) and len(spec) > 1 else {}
            await self._load_one(name, config)

    async def _load_one(self, name: str, config: Any) -> PluginInfo:
        # Drop any stale record first so reloads end up with a single,
        # current entry.
        self._plugins = [p for p in self._plugins if p.name != name]
        try:
            mod = importlib.import_module(name)
            # importlib caches modules — reload picks up source changes.
            mod = importlib.reload(mod)
            if hasattr(mod, "server"):
                hooks = await mod.server(config)
                self._register_plugin_hooks(name, hooks)
                info = PluginInfo(name=name, status="loaded")
                self._plugins.append(info)
                self._plugin_configs[name] = config
                logger.info("loaded plugin", name=name)
                return info
            else:
                logger.warn("plugin has no server export", name=name)
                info = PluginInfo(name=name, status="failed", error="no server export")
                self._plugins.append(info)
                return info
        except Exception as e:
            logger.warn("failed to load plugin", name=name, error=str(e))
            info = PluginInfo(name=name, status="failed", error=str(e))
            self._plugins.append(info)
            return info

    def _register_plugin_hooks(self, plugin_name: str, hooks: dict[str, Any]) -> None:
        tracked = self._plugin_hooks.setdefault(plugin_name, [])
        for hook_name, fn in hooks.items():
            if callable(fn):
                self._hooks.setdefault(hook_name, []).append(fn)
                tracked.append((hook_name, fn))

    # Kept for backwards compatibility with any out-of-tree callers.
    def _register_hooks(self, hooks: dict[str, Any]) -> None:
        self._register_plugin_hooks("_anonymous", hooks)

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
        if plugin_name not in self._plugin_configs and not any(p.name == plugin_name for p in self._plugins):
            return False
        # Remove every hook registered by this plugin. Each tuple is
        # (hook_name, callable); the callable identity is unique per
        # plugin load so list.remove picks the right one.
        for hook_name, fn in self._plugin_hooks.pop(plugin_name, []):
            bucket = self._hooks.get(hook_name)
            if bucket is None:
                continue
            with contextlib.suppress(ValueError):
                bucket.remove(fn)
            if not bucket:
                self._hooks.pop(hook_name, None)
        self._plugins = [p for p in self._plugins if p.name != plugin_name]
        self._plugin_configs.pop(plugin_name, None)
        logger.info("unloaded plugin", name=plugin_name)
        return True

    async def reload(self, plugin_name: str) -> PluginInfo:
        """Hot-reload a plugin: unload, re-import, re-register hooks.

        Safe to call at runtime — existing in-flight hook invocations
        (running inside ``trigger()``) iterate over a snapshot of the
        hook list so they are not disturbed by the swap.
        """
        config = self._plugin_configs.get(plugin_name, {})
        self.unload(plugin_name)
        return await self._load_one(plugin_name, config)

    def list_plugins(self) -> list[dict[str, Any]]:
        return [{"name": p.name, "status": p.status, "error": p.error} for p in self._plugins]

    def list_hooks(self) -> dict[str, int]:
        """List all registered hooks and their handler count."""
        return {name: len(fns) for name, fns in self._hooks.items() if fns}
