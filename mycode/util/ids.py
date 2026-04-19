"""ID generation utilities.

Provides ordered ID generation using ULID.
Session IDs are descending (newest first), Message/Part IDs are ascending.
"""

from __future__ import annotations

from ulid import ULID

# Crockford Base32 alphabet used by ULID (uppercase)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_REVERSE = _CROCKFORD[::-1]
_INVERT_TABLE = str.maketrans(_CROCKFORD, _REVERSE)


def ascending() -> str:
    """Generate an ascending (time-ordered) ULID string."""
    return str(ULID())


def descending(existing: str | None = None) -> str:
    """Generate a descending ID (reverse sort order, URL-safe).

    Inverts each character within the Crockford Base32 alphabet so that
    the lexicographic order is reversed while keeping the result URL-safe
    (only alphanumeric characters).
    """
    if existing:
        return existing
    raw = str(ULID())
    return raw.upper().translate(_INVERT_TABLE)


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
