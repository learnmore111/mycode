"""Hashing utilities."""

from __future__ import annotations

import hashlib


def fast(data: str) -> str:
    """Fast non-cryptographic hash (MD5 hex). Used for cache keys, not security."""
    return hashlib.md5(data.encode()).hexdigest()


def sha256(data: str) -> str:
    """SHA-256 hex digest."""
    return hashlib.sha256(data.encode()).hexdigest()
