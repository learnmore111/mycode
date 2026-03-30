"""Session memory module for auto-saving conversation notes.

This module provides functionality to automatically summarize and save
session conversations when they end, similar to claude-memory skill.
"""

from opencode.session.memory.memory import (
    SessionMemory,
    SessionNote,
    load_recent_notes,
    save_session_note,
)

__all__ = [
    "SessionMemory",
    "SessionNote",
    "load_recent_notes",
    "save_session_note",
]
