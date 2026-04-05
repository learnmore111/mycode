"""Memory directory management — structured memory system.

- Four memory types: user, feedback, project, reference
- Frontmatter-based memory files (YAML header + markdown body)
- MEMORY.md index file as entry point
- Path safety validation (prevents directory traversal)
- Memory file scanning with frontmatter parsing
- Memory freshness tracking
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from opencode.util import log as logmod

logger = logmod.create(service="session.memory.memdir")

# ---------------------------------------------------------------------------
# Memory types
# ---------------------------------------------------------------------------

MemoryType = Literal["user", "feedback", "project", "reference"]

MEMORY_TYPE_DESCRIPTIONS: dict[MemoryType, str] = {
    "user": "User role, goals, preferences, knowledge level",
    "feedback": "User guidance on work style (corrections + confirmations)",
    "project": "Ongoing work, goals, events, deadlines",
    "reference": "Pointers to information in external systems",
}

# What NOT to save as memories (can be derived from codebase)
MEMORY_EXCLUSIONS = [
    "Code patterns, architecture, file paths (use grep/git/CLAUDE.md)",
    "Git history (git log/blame is authoritative)",
    "Debug solutions (fix is in code, context in commit message)",
    "Content already in CLAUDE.md / .opencode instructions",
    "Temporary task details",
]

# Limits
MAX_MEMORY_FILES = 200
MAX_MEMORY_INDEX_LINES = 200
MAX_MEMORY_INDEX_SIZE = 25 * 1024  # 25KB

# Frontmatter regex
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single memory file with parsed frontmatter."""
    path: str                  # Absolute path to memory file
    name: str                  # Memory name from frontmatter
    description: str           # One-line description
    memory_type: MemoryType    # user/feedback/project/reference
    content: str               # Full markdown body (after frontmatter)
    mtime_ms: float = 0.0     # File modification time in milliseconds
    size_bytes: int = 0        # File size

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def relative_path(self) -> str:
        """Return just the filename without extension for display."""
        return Path(self.path).stem


@dataclass
class MemoryIndex:
    """The MEMORY.md index with all discovered memories."""
    entries: list[MemoryEntry] = field(default_factory=list)
    base_dir: str = ""

    @property
    def count(self) -> int:
        return len(self.entries)

    def by_type(self, memory_type: MemoryType) -> list[MemoryEntry]:
        return [e for e in self.entries if e.memory_type == memory_type]


# ---------------------------------------------------------------------------
# Path management
# ---------------------------------------------------------------------------


def memory_base_dir(project_path: str) -> str:
    """Get the memory base directory for a project.

    Priority:
    1. OPENCODE_MEMORY_DIR env var (full override)
    2. <project>/.opencode/memory/
    """
    override = os.environ.get("OPENCODE_MEMORY_DIR")
    if override and os.path.isabs(override):
        return override
    return os.path.join(project_path, ".opencode", "memory")


def memdir_path(project_path: str) -> str:
    """Get the memdir directory (where structured memories live)."""
    return os.path.join(memory_base_dir(project_path), "memdir")


def validate_memory_path(path: str) -> str | None:
    """Validate a memory file path for safety.

    Returns error message if unsafe, None if safe.
    Rejects:
    - Relative paths (../)
    - Root / near-root paths
    - Null bytes
    - Non-absolute paths
    """
    if not path:
        return "Empty path"

    if "\x00" in path:
        return "Path contains null byte"

    if not os.path.isabs(path):
        return f"Path must be absolute: {path}"

    resolved = os.path.realpath(path)

    # Check for near-root paths (e.g. /a, /b)
    parts = Path(resolved).parts
    if len(parts) <= 2:
        return f"Path too close to root: {resolved}"

    # Check for directory traversal
    if ".." in Path(path).parts:
        return f"Path contains directory traversal: {path}"

    return None


def sanitize_memory_name(name: str) -> str:
    """Sanitize a memory name for use as filename.

    Converts to lowercase, replaces spaces/special chars with underscores.
    """
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name.strip().lower())
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "unnamed"


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from a memory file.

    Returns (metadata_dict, body_text).
    If no frontmatter found, returns ({}, full_content).
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_block = match.group(1)
    body = match.group(2).strip()

    # Simple YAML parser (key: value pairs only)
    metadata: dict[str, str] = {}
    for line in yaml_block.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value:
                metadata[key] = value

    return metadata, body


def format_frontmatter(
    name: str,
    description: str,
    memory_type: MemoryType,
    body: str,
) -> str:
    """Format a memory file with YAML frontmatter."""
    return f"""---
name: {name}
description: {description}
type: {memory_type}
---

{body}
"""


# ---------------------------------------------------------------------------
# Memory file scanning
# ---------------------------------------------------------------------------


def scan_memory_files(project_path: str) -> list[MemoryEntry]:
    """Scan the memdir directory for all memory files with frontmatter.

    Reads up to MAX_MEMORY_FILES files from the memdir directory.
    Parses frontmatter to extract name, description, and type.
    """
    base = memdir_path(project_path)
    if not os.path.isdir(base):
        return []

    entries: list[MemoryEntry] = []
    count = 0

    for name in sorted(os.listdir(base)):
        if count >= MAX_MEMORY_FILES:
            break
        fp = os.path.join(base, name)
        if not os.path.isfile(fp):
            continue
        if not name.endswith(".md"):
            continue

        try:
            content = Path(fp).read_text(encoding="utf-8")
            stat = os.stat(fp)
            metadata, body = parse_frontmatter(content)

            mem_type = metadata.get("type", "project")
            if mem_type not in ("user", "feedback", "project", "reference"):
                mem_type = "project"

            entries.append(MemoryEntry(
                path=fp,
                name=metadata.get("name", Path(fp).stem),
                description=metadata.get("description", ""),
                memory_type=mem_type,  # type: ignore[arg-type]
                content=body,
                mtime_ms=stat.st_mtime * 1000,
                size_bytes=stat.st_size,
            ))
            count += 1
        except (IOError, UnicodeDecodeError) as e:
            logger.warn("failed to read memory file", path=fp, error=str(e))
            continue

    return entries


def scan_memory_index(project_path: str) -> MemoryIndex:
    """Scan and build a MemoryIndex from the project's memdir."""
    entries = scan_memory_files(project_path)
    return MemoryIndex(
        entries=entries,
        base_dir=memdir_path(project_path),
    )


# ---------------------------------------------------------------------------
# Memory manifest formatting (for retrieval)
# ---------------------------------------------------------------------------


def format_memory_manifest(entries: list[MemoryEntry]) -> str:
    """Format memory entries as a manifest for LLM-based retrieval.

    Output format (one per line):
    <filename> [type] — <description>
    """
    lines: list[str] = []
    for entry in entries:
        desc = entry.description or "(no description)"
        lines.append(f"{entry.filename} [{entry.memory_type}] — {desc}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MEMORY.md index management
# ---------------------------------------------------------------------------


def build_memory_index_content(entries: list[MemoryEntry]) -> str:
    """Build the content for MEMORY.md index file."""
    lines: list[str] = [
        "# Memory Index",
        "",
        f"Total memories: {len(entries)}",
        "",
    ]

    for mem_type in ("user", "feedback", "project", "reference"):
        typed = [e for e in entries if e.memory_type == mem_type]
        if not typed:
            continue
        type_desc = MEMORY_TYPE_DESCRIPTIONS.get(mem_type, mem_type)  # type: ignore[arg-type]
        lines.append(f"## {mem_type.title()} ({type_desc})")
        lines.append("")
        for entry in typed:
            desc = entry.description or "(no description)"
            lines.append(f"- **{entry.name}**: {desc}")
        lines.append("")

    return "\n".join(lines[:MAX_MEMORY_INDEX_LINES])


def update_memory_index(project_path: str) -> str:
    """Rebuild and write the MEMORY.md index file. Returns the index path."""
    entries = scan_memory_files(project_path)
    content = build_memory_index_content(entries)

    base = memdir_path(project_path)
    os.makedirs(base, exist_ok=True)
    index_path = os.path.join(base, "MEMORY.md")
    Path(index_path).write_text(content, encoding="utf-8")
    logger.debug("updated memory index", path=index_path, count=len(entries))
    return index_path


# ---------------------------------------------------------------------------
# Memory CRUD operations
# ---------------------------------------------------------------------------


def save_memory(
    project_path: str,
    name: str,
    description: str,
    memory_type: MemoryType,
    content: str,
) -> str:
    """Save a new memory file to the memdir. Returns the file path."""
    base = memdir_path(project_path)
    os.makedirs(base, exist_ok=True)

    filename = f"{memory_type}_{sanitize_memory_name(name)}.md"
    filepath = os.path.join(base, filename)

    text = format_frontmatter(name, description, memory_type, content)
    Path(filepath).write_text(text, encoding="utf-8")
    logger.info("saved memory", name=name, type=memory_type, path=filepath)

    # Rebuild index
    update_memory_index(project_path)
    return filepath


def delete_memory(project_path: str, filename: str) -> bool:
    """Delete a memory file. Returns True if deleted."""
    base = memdir_path(project_path)
    filepath = os.path.join(base, filename)

    # Safety check
    error = validate_memory_path(filepath)
    if error:
        logger.warn("refused to delete memory", path=filepath, error=error)
        return False

    if not os.path.isfile(filepath):
        return False

    os.unlink(filepath)
    logger.info("deleted memory", path=filepath)

    # Rebuild index
    update_memory_index(project_path)
    return True


def update_memory(
    project_path: str,
    filename: str,
    *,
    name: str | None = None,
    description: str | None = None,
    memory_type: MemoryType | None = None,
    content: str | None = None,
) -> str | None:
    """Update an existing memory file. Returns path if updated, None if not found."""
    base = memdir_path(project_path)
    filepath = os.path.join(base, filename)

    if not os.path.isfile(filepath):
        return None

    # Read existing
    existing_content = Path(filepath).read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(existing_content)

    # Merge updates
    final_name = name or metadata.get("name", Path(filepath).stem)
    final_desc = description or metadata.get("description", "")
    final_type = memory_type or metadata.get("type", "project")
    if final_type not in ("user", "feedback", "project", "reference"):
        final_type = "project"
    final_body = content if content is not None else body

    text = format_frontmatter(final_name, final_desc, final_type, final_body)  # type: ignore[arg-type]
    Path(filepath).write_text(text, encoding="utf-8")
    logger.info("updated memory", name=final_name, path=filepath)

    # Rebuild index
    update_memory_index(project_path)
    return filepath


# ---------------------------------------------------------------------------
# Memory context formatting (for agent prompt injection)
# ---------------------------------------------------------------------------


def format_memories_for_context(
    entries: list[MemoryEntry],
    *,
    include_freshness: bool = True,
) -> str:
    """Format selected memories for injection into agent system prompt.

    Wraps each memory in XML tags with type and freshness info.
    """
    from opencode.session.memory.memory import memory_freshness_note

    if not entries:
        return ""

    lines: list[str] = ["<relevant_memories>"]

    for entry in entries:
        lines.append(f'<memory name="{entry.name}" type="{entry.memory_type}">')

        # Freshness warning for stale memories
        if include_freshness and entry.mtime_ms > 0:
            note = memory_freshness_note(entry.mtime_ms)
            if note:
                lines.append(note)

        lines.append(entry.content)
        lines.append("</memory>")

    lines.append("</relevant_memories>")
    return "\n".join(lines)
