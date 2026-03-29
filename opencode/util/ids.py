"""ID generation utilities.

Provides ordered ID generation using ULID, matching the original src/id/ module.
Session IDs are descending (newest first), Message/Part IDs are ascending.
"""

from __future__ import annotations

from ulid import ULID


def ascending() -> str:
    """Generate an ascending (time-ordered) ULID string."""
    return str(ULID())


def descending(existing: str | None = None) -> str:
    """Generate a descending ID. If existing is provided, return it as-is.

    In the original, descending IDs are used for sessions so that newer sessions
    sort first. We invert the ULID timestamp bits.
    """
    if existing:
        return existing
    ulid = ULID()
    # Invert the bytes to create a descending order
    raw = ulid.bytes
    inverted = bytes(0xFF - b for b in raw)
    return ULID(inverted).str


def session_id(existing: str | None = None) -> str:
    """Generate a session ID (descending order)."""
    return descending(existing)


def message_id() -> str:
    """Generate a message ID (ascending order)."""
    return ascending()


def part_id() -> str:
    """Generate a part ID (ascending order)."""
    return ascending()


def permission_id() -> str:
    """Generate a permission request ID (ascending order)."""
    return ascending()
