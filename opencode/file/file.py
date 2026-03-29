"""File operations. Equivalent to src/file/index.ts."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from opencode.file import ripgrep
from opencode.project.instance import current_or_none

BINARY_EXT = {"exe","dll","bin","so","dylib","zip","tar","gz","7z","pdf","wasm","sqlite","db"}
IMAGE_EXT = {"png","jpg","jpeg","gif","bmp","webp","ico","svg","avif"}
MIME = {"png":"image/png","jpg":"image/jpeg","jpeg":"image/jpeg","gif":"image/gif",
    "webp":"image/webp","svg":"image/svg+xml","bmp":"image/bmp"}


async def read(file_path: str) -> dict:
    inst = current_or_none()
    base = inst.directory if inst else os.getcwd()
    full = os.path.join(base, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(full):
        return {"type": "text", "content": ""}
    ext = Path(full).suffix.lstrip(".").lower()
    if ext in IMAGE_EXT:
        data = Path(full).read_bytes()
        return {"type": "text", "content": base64.b64encode(data).decode(), "encoding": "base64",
                "mime_type": MIME.get(ext, f"image/{ext}")}
    if ext in BINARY_EXT:
        return {"type": "binary", "content": ""}
    try:
        return {"type": "text", "content": Path(full).read_text(encoding="utf-8", errors="replace")}
    except Exception:
        return {"type": "binary", "content": ""}


async def search(query: str, *, limit: int = 100, file_type: str = "all") -> list[str]:
    """Fuzzy search files in the project."""
    inst = current_or_none()
    cwd = inst.directory if inst else os.getcwd()
    files: list[str] = []
    async for f in ripgrep.files(cwd=cwd):
        files.append(f)
    if not query:
        return files[:limit]
    from rapidfuzz import process as fuzz_proc
    results = fuzz_proc.extract(query, files, limit=limit)
    return [r[0] for r in results]


async def list_dir(dir_path: str | None = None) -> list[dict]:
    inst = current_or_none()
    base = inst.directory if inst else os.getcwd()
    resolved = os.path.join(base, dir_path) if dir_path else base
    if not os.path.isdir(resolved):
        return []
    entries = []
    for entry in sorted(os.scandir(resolved), key=lambda e: (not e.is_dir(), e.name)):
        if entry.name in (".git", ".DS_Store"):
            continue
        entries.append({"name": entry.name, "type": "directory" if entry.is_dir() else "file",
                        "path": os.path.relpath(entry.path, base)})
    return entries
