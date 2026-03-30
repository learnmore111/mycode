"""Session memory module — dual-layer memory system for AI agent context.

Two types of memory:
1. Session Summary Note — high-level technical context, updated every N turns.
2. Interaction Log — near-lossless per-turn record of queries, tool calls, results.
"""

from opencode.session.memory.memory import (
    InteractionEntry,
    InteractionLog,
    SessionMemory,
    SessionNote,
    create_interaction_log,
    load_recent_notes,
    save_session_note,
)

__all__ = [
    "InteractionEntry",
    "InteractionLog",
    "SessionMemory",
    "SessionNote",
    "create_interaction_log",
    "load_recent_notes",
    "save_session_note",
]
