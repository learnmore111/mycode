"""Wildcard pattern matching.

Provides glob-style pattern matching used by the permission system.
"""

from __future__ import annotations

import fnmatch


def match(value: str, pattern: str) -> bool:
    """Check if a value matches a wildcard pattern.

    Supports '*' for any sequence of characters.
    """
    return fnmatch.fnmatch(value, pattern)


def match_any(value: str, patterns: list[str]) -> bool:
    """Check if a value matches any of the given patterns."""
    return any(match(value, p) for p in patterns)
