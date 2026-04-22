"""Flow registry: discover, list and resolve orchestration files.

Discovery paths (later overrides earlier):

1. Built-in: :mod:`mycode.orchestration.flows` (shipped ``*.yaml``)
2. Global:   ``$MYCODE_HOME/orchestrations/`` (defaults to ``~/.mycode``)
3. Project:  ``<project_root>/.mycode/orchestrations/``

The registry is **lazy**: it only scans directories when queried.
Loading a flow goes through :func:`~mycode.orchestration.topology.loader.load_file`
and supports ``extends: <other_flow>`` inheritance (resolved internally).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mycode.orchestration.topology.loader import OrchestrationLoadError, load_file
from mycode.orchestration.topology.validator import validate

if TYPE_CHECKING:
    from mycode.orchestration.topology.schema import OrchestrationSpec

_SUPPORTED_EXTS = (".yaml", ".yml", ".json")

# Path to the built-in flows directory (shipped with the package)
_BUILTIN_FLOWS_DIR = Path(__file__).resolve().parent.parent / "flows"


@dataclass(frozen=True)
class FlowInfo:
    """Metadata about a discovered flow (cheap to construct; no YAML parse)."""

    name: str
    path: Path
    source: str  # "builtin" | "global" | "project"


class FlowRegistry:
    """Discovers and resolves orchestration flows across three layers."""

    def __init__(
        self,
        *,
        project_dir: str | Path | None = None,
        global_dir: str | Path | None = None,
        builtin_dir: str | Path | None = None,
    ) -> None:
        self.builtin_dir = Path(builtin_dir) if builtin_dir else _BUILTIN_FLOWS_DIR
        self.global_dir = Path(global_dir) if global_dir else _default_global_dir()
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self._cache: dict[str, FlowInfo] | None = None

    # --- discovery --------------------------------------------------------

    def list_flows(self, *, refresh: bool = False) -> list[FlowInfo]:
        """Return all discovered flows, later layers overriding earlier ones."""
        if refresh:
            self._cache = None
        self._ensure_loaded()
        assert self._cache is not None
        return sorted(self._cache.values(), key=lambda f: (f.source, f.name))

    def resolve(self, name: str) -> FlowInfo:
        """Find a flow by name. Raises :class:`FileNotFoundError` if missing."""
        self._ensure_loaded()
        assert self._cache is not None
        info = self._cache.get(name)
        if info is None:
            known = ", ".join(sorted(self._cache.keys())) or "(none)"
            raise FileNotFoundError(f"orchestration flow not found: {name!r}. Known: {known}")
        return info

    # --- loading ----------------------------------------------------------

    def load(
        self,
        name: str,
        *,
        vars_override: dict[str, Any] | None = None,
    ) -> OrchestrationSpec:
        """Resolve + parse + validate a flow by name."""
        info = self.resolve(name)
        spec = load_file(
            info.path,
            vars_override=vars_override,
            parent_resolver=self._resolve_parent,
        )
        validate(spec)
        return spec

    def _resolve_parent(self, parent_name: str) -> OrchestrationSpec:
        """Used by :func:`load_file` when a flow extends another by name."""
        info = self.resolve(parent_name)
        parent = load_file(info.path, parent_resolver=self._resolve_parent)
        return parent

    # --- internals --------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._cache is not None:
            return

        cache: dict[str, FlowInfo] = {}
        # Order matters: later layer overrides earlier.
        for directory, source in self._iter_sources():
            if not directory or not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                if entry.is_dir():
                    continue
                if entry.suffix.lower() not in _SUPPORTED_EXTS:
                    continue
                name = entry.stem
                cache[name] = FlowInfo(name=name, path=entry.resolve(), source=source)
        self._cache = cache

    def _iter_sources(self) -> list[tuple[Path, str]]:
        out: list[tuple[Path, str]] = [(self.builtin_dir, "builtin"), (self.global_dir, "global")]
        if self.project_dir is not None:
            out.append((self.project_dir / ".mycode" / "orchestrations", "project"))
        return out


# --- helpers ----------------------------------------------------------------


def _default_global_dir() -> Path:
    home = os.environ.get("MYCODE_HOME")
    if home:
        return Path(home).expanduser() / "orchestrations"
    return Path.home() / ".mycode" / "orchestrations"


_default_registry: FlowRegistry | None = None


def get_default_registry(project_dir: str | Path | None = None, *, refresh: bool = False) -> FlowRegistry:
    """Lazy singleton; pass ``project_dir`` on first call to enable project scope."""
    global _default_registry
    if _default_registry is None or refresh:
        _default_registry = FlowRegistry(project_dir=project_dir)
    elif project_dir is not None and _default_registry.project_dir is None:
        # Upgrade with project dir if not yet set
        _default_registry = FlowRegistry(project_dir=project_dir)
    return _default_registry


# Re-export for convenience
__all__ = ["FlowInfo", "FlowRegistry", "OrchestrationLoadError", "get_default_registry"]
