"""Error types and utilities.

Provides a NamedError base class similar to the original @mycode-ai/util/error.
"""

from __future__ import annotations

from typing import Any


class NamedError(Exception):
    """Base error class with structured data, mirroring the TS NamedError."""

    name: str = "NamedError"

    def __init__(self, data: dict[str, Any] | None = None, *, message: str | None = None, cause: BaseException | None = None):
        self.data = data or {}
        super().__init__(message or self.name, cause)
        if cause:
            self.__cause__ = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "message": str(self),
            "data": self.data,
        }

    @classmethod
    def create(cls, name: str) -> type[NamedError]:
        """Factory to create a named error subclass."""
        return type(name, (cls,), {"name": name})


class NotFoundError(NamedError):
    name = "NotFoundError"


class UnknownError(NamedError):
    name = "UnknownError"


def error_message(e: Any) -> str:
    """Extract a human-readable message from any error."""
    if isinstance(e, Exception):
        return str(e)
    return str(e)
