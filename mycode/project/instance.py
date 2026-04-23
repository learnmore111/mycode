"""Project instance context management.

Manages the current project context, providing directory/worktree/project info
to all services via contextvars.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

T = TypeVar("T")


@dataclass
class ProjectInfo:
    """Minimal project info held in the instance context."""

    id: str
    worktree: str
    vcs: str | None = None  # "git" or None
    name: str | None = None


@dataclass
class InstanceContext:
    """Holds all per-project state. Set via contextvar during request processing."""

    directory: str
    worktree: str
    project: ProjectInfo
    _state: dict[str, Any] = field(default_factory=dict)

    def contains_path(self, p: str) -> bool:
        """Check if a path is within the project worktree."""
        try:
            resolved = str(Path(p).resolve())
            wt = str(Path(self.worktree).resolve())
            return resolved.startswith(wt)
        except (ValueError, OSError):
            return False


_instance_var: ContextVar[InstanceContext | None] = ContextVar("instance", default=None)


def current() -> InstanceContext:
    """Get the current instance context. Raises if not set."""
    ctx = _instance_var.get()
    if ctx is None:
        raise RuntimeError("No instance context is active. Call within Instance.provide().")
    return ctx


def current_or_none() -> InstanceContext | None:
    """Get the current instance context, or None."""
    return _instance_var.get()


class _InstanceToken:
    """RAII token for instance context."""

    def __init__(self, token: Token[InstanceContext | None]):
        self._token = token

    def reset(self) -> None:
        _instance_var.reset(self._token)


def set_context(ctx: InstanceContext) -> _InstanceToken:
    """Set the instance context, returning a token for reset."""
    token = _instance_var.set(ctx)
    return _InstanceToken(token)


async def provide[T](
    directory: str,
    fn: Callable[[], Coroutine[Any, Any, T]],
    project: ProjectInfo | None = None,
) -> T:
    """Run an async function within a project instance context.

    This is the primary way to establish context for project operations.

    If ``project`` is not supplied, the project is auto-discovered from
    ``directory`` via ``mycode.project.project.from_directory`` (git root →
    stable project id). Callers that already resolved the project (e.g.
    ``mycode run``/``serve``) should still pass it explicitly to avoid a
    duplicate discovery call.
    """
    resolved = str(Path(directory).resolve())

    if project is None:
        # Import lazily to avoid a circular import: project.project already
        # imports ProjectInfo from this module at module load time.
        from mycode.project.project import from_directory as _from_directory

        try:
            project = await _from_directory(resolved)
        except Exception:
            # Fall back to a minimal "global" project so CLI commands that
            # don't strictly need a real project (e.g. running outside any
            # repo) still work.
            project = ProjectInfo(
                id="global",
                worktree=resolved,
                vcs=None,
            )

    ctx = InstanceContext(
        directory=resolved,
        worktree=project.worktree,
        project=project,
    )

    token = set_context(ctx)
    try:
        return await fn()
    finally:
        token.reset()
