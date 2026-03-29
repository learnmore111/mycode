"""Slug generation utility."""

from __future__ import annotations

import random
import string


def create(length: int = 8) -> str:
    """Create a random URL-friendly slug."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))
