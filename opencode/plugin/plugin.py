"""Plugin system. Equivalent to src/plugin/index.ts."""
from __future__ import annotations
import importlib
from typing import Any, Callable
from opencode.util import log as logmod

logger = logmod.create(service="plugin")

Hook = Callable[..., Any]


class PluginManager:
    """Manages loaded plugins and their hooks."""
    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {}
        self._plugins: list[dict[str, Any]] = []

    async def init(self, plugin_specs: list[Any] | None = None) -> None:
        if not plugin_specs:
            return
        for spec in plugin_specs:
            name = spec if isinstance(spec, str) else spec[0]
            try:
                # Try to import as Python module
                mod = importlib.import_module(name)
                if hasattr(mod, "server"):
                    hooks = await mod.server({})
                    self._register_hooks(hooks)
                    self._plugins.append({"name": name, "status": "loaded"})
                    logger.info("loaded plugin", name=name)
                else:
                    logger.warn("plugin has no server export", name=name)
            except Exception as e:
                logger.warn("failed to load plugin", name=name, error=str(e))
                self._plugins.append({"name": name, "status": "failed", "error": str(e)})

    def _register_hooks(self, hooks: dict[str, Any]) -> None:
        for name, fn in hooks.items():
            if callable(fn):
                self._hooks.setdefault(name, []).append(fn)

    async def trigger(self, hook_name: str, input: Any, output: Any) -> Any:
        for fn in self._hooks.get(hook_name, []):
            try:
                await fn(input, output)
            except Exception as e:
                logger.error("hook failed", hook=hook_name, error=str(e))
        return output

    def list_plugins(self) -> list[dict[str, Any]]:
        return list(self._plugins)
