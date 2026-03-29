"""JSON file-based storage.

Provides key-value storage backed by JSON files on disk.
Equivalent to src/storage/storage.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from opencode.util import filesystem as fs
from opencode.util import log as logmod
from opencode.util.paths import GlobalPaths

logger = logmod.create(service="storage")

T = TypeVar("T")


def _storage_dir() -> Path:
    return GlobalPaths.data() / "storage"


def _path(key: list[str]) -> Path:
    return _storage_dir().joinpath(*key).with_suffix(".json")


async def read(key: list[str]) -> Any:
    """Read a JSON value by key path."""
    p = _path(key)
    if not p.exists():
        raise FileNotFoundError(f"Storage key not found: {'/'.join(key)}")
    return await fs.read_json(str(p))


async def write(key: list[str], content: Any) -> None:
    """Write a JSON value to the given key path."""
    p = _path(key)
    await fs.write_json(str(p), content)


async def update(key: list[str], fn: Any) -> Any:
    """Read, mutate, and write back a JSON value."""
    data = await read(key)
    fn(data)
    await write(key, data)
    return data


async def remove(key: list[str]) -> None:
    """Remove a stored value."""
    p = _path(key)
    await fs.remove(str(p))


async def list_keys(prefix: list[str]) -> list[list[str]]:
    """List all keys under a prefix."""
    base = _storage_dir().joinpath(*prefix)
    if not base.exists():
        return []
    result: list[list[str]] = []
    for p in base.rglob("*.json"):
        rel = p.relative_to(_storage_dir())
        key = list(rel.with_suffix("").parts)
        result.append(key)
    result.sort()
    return result


def exists(key: list[str]) -> bool:
    """Check if a key exists."""
    return _path(key).exists()
