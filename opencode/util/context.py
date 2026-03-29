"""Context management using contextvars.

Replaces Effect-TS Service/Layer dependency injection with Python's contextvar.
Each request/instance gets its own context with lazily-initialized services.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from opencode.project.instance import InstanceContext

T = TypeVar("T")

_instance_ctx: ContextVar[InstanceContext | None] = ContextVar("instance_ctx", default=None)


def get_instance() -> InstanceContext:
    """Get the current instance context. Raises if not set."""
    ctx = _instance_ctx.get()
    if ctx is None:
        raise RuntimeError("No instance context is active. Call within Instance.provide().")
    return ctx


def set_instance(ctx: InstanceContext) -> None:
    """Set the current instance context."""
    _instance_ctx.set(ctx)


def has_instance() -> bool:
    """Check if an instance context is active."""
    return _instance_ctx.get() is not None
