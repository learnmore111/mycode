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
    """Generate a descending ID. If existing is provided, return it as-is."""
    if existing:
        return existing
    # Use a simple approach: prefix with inverted timestamp
    ulid = ULID()
    # Invert the ULID string chars to reverse sort order
    raw = str(ulid)
    inverted = "".join(chr(0x7E - ord(c) + 0x30) if c.isalnum() else c for c in raw)
    return inverted


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
