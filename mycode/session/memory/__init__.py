"""Session memory module — unified memory system for AI agent context.

Two-layer architecture:
1. SessionMemory — per-session JSONL (rolling summary + per-turn records)
2. memdir — structured long-term memories (user/feedback/project/reference)
   with frontmatter, MEMORY.md index, retrieval, and auto-extraction
"""

from mycode.session.memory.extractor import (
    ExtractionResult,
    extract_memories,
    save_extracted_memories,
)
from mycode.session.memory.memdir import (
    MemoryEntry,
    MemoryIndex,
    MemoryType,
    build_memory_index_content,
    delete_memory,
    format_frontmatter,
    format_memories_for_context,
    format_memory_manifest,
    load_memory_index,
    memdir_path,
    memory_base_dir,
    memory_index_path,
    parse_frontmatter,
    sanitize_memory_name,
    save_memory,
    scan_memory_files,
    scan_memory_index,
    update_memory,
    update_memory_index,
    validate_memory_path,
)
from mycode.session.memory.memory import (
    InteractionEntry,
    SessionMemory,
    SessionSummary,
    create_session_memory,
    load_recent_notes,
    memory_age_days,
    memory_age_text,
    memory_freshness_note,
)
from mycode.session.memory.retrieval import (
    build_memory_context,
    find_relevant_memories,
    find_relevant_memories_llm,
)

__all__ = [
    # SessionMemory (per-session JSONL)
    "InteractionEntry",
    "SessionMemory",
    "SessionSummary",
    "create_session_memory",
    "load_recent_notes",
    "memory_age_days",
    "memory_age_text",
    "memory_freshness_note",
    # memdir (structured long-term memories)
    "MemoryEntry",
    "MemoryIndex",
    "MemoryType",
    "build_memory_index_content",
    "delete_memory",
    "format_frontmatter",
    "format_memories_for_context",
    "format_memory_manifest",
    "load_memory_index",
    "memory_index_path",
    "memdir_path",
    "memory_base_dir",
    "parse_frontmatter",
    "sanitize_memory_name",
    "save_memory",
    "scan_memory_files",
    "scan_memory_index",
    "update_memory",
    "update_memory_index",
    "validate_memory_path",
    # Retrieval
    "build_memory_context",
    "find_relevant_memories",
    "find_relevant_memories_llm",
    # Extraction
    "ExtractionResult",
    "extract_memories",
    "save_extracted_memories",
]
