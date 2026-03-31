"""Session memory module — unified memory system for AI agent context.

Single JSONL file per session containing rolling summary + per-turn records.
Every 3 turns, LLM updates the summary and refines turn descriptions.
"""

from opencode.session.memory.memory import (
    InteractionEntry,
    SessionMemory,
    SessionSummary,
    create_session_memory,
    load_recent_notes,
    memory_age_days,
    memory_age_text,
    memory_freshness_note,
)

__all__ = [
    "InteractionEntry",
    "SessionMemory",
    "SessionSummary",
    "create_session_memory",
    "load_recent_notes",
    "memory_age_days",
    "memory_age_text",
    "memory_freshness_note",
]
