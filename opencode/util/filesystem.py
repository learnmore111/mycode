"""Filesystem utilities.

Provides async and sync file operations, mirroring src/util/filesystem.ts.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

import aiofiles


def resolve(p: str) -> str:
    """Resolve a path to an absolute path."""
    return str(Path(p).resolve())


def exists(p: str) -> bool:
    """Check if a path exists (sync)."""
    return Path(p).exists()


async def exists_async(p: str) -> bool:
    """Check if a path exists (async)."""
    return Path(p).exists()


def is_dir(p: str) -> bool:
    """Check if path is a directory (sync)."""
    return Path(p).is_dir()


def stat(p: str) -> os.stat_result | None:
    """Get file stat, return None on error."""
    try:
        return os.stat(p)
    except OSError:
        return None


async def read_text(p: str, encoding: str = "utf-8") -> str:
    """Read a text file asynchronously."""
    async with aiofiles.open(p, encoding=encoding) as f:
        return await f.read()


async def read_bytes(p: str) -> bytes:
    """Read a binary file asynchronously."""
    async with aiofiles.open(p, "rb") as f:
        return await f.read()


async def write_text(p: str, content: str, encoding: str = "utf-8") -> None:
    """Write text to a file, creating parent directories."""
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(p, "w", encoding=encoding) as f:
        await f.write(content)


async def write_bytes(p: str, content: bytes) -> None:
    """Write bytes to a file, creating parent directories."""
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(p, "wb") as f:
        await f.write(content)


async def read_json(p: str) -> Any:
    """Read and parse a JSON file."""
    text = await read_text(p)
    return json.loads(text)


async def write_json(p: str, data: Any) -> None:
    """Write data as JSON to a file."""
    content = json.dumps(data, indent=2, ensure_ascii=False)
    await write_text(p, content)


def read_text_sync(p: str, encoding: str = "utf-8") -> str:
    """Read a text file synchronously."""
    return Path(p).read_text(encoding=encoding)


def read_json_sync(p: str) -> Any:
    """Read and parse a JSON file synchronously."""
    return json.loads(read_text_sync(p))


def write_json_sync(p: str, data: Any) -> None:
    """Write data as JSON synchronously."""
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def mime_type(p: str) -> str:
    """Get the MIME type of a file."""
    mime, _ = mimetypes.guess_type(p)
    return mime or "application/octet-stream"


async def ensure_dir(p: str) -> None:
    """Ensure a directory exists."""
    Path(p).mkdir(parents=True, exist_ok=True)


async def remove(p: str) -> None:
    """Remove a file, ignoring errors."""
    with contextlib.suppress(OSError):
        os.unlink(p)
