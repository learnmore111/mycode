"""Event definitions for the bus system.

Equivalent to src/bus/bus-event.ts — a typed event registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EventDef:
    """An event type definition."""
    type: str


@dataclass
class Event:
    """A concrete event instance."""
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


# --- Session Events ---
SESSION_CREATED = EventDef("session.created")
SESSION_UPDATED = EventDef("session.updated")
SESSION_DELETED = EventDef("session.deleted")
SESSION_DIFF = EventDef("session.diff")
SESSION_ERROR = EventDef("session.error")

# --- Message Events ---
MESSAGE_UPDATED = EventDef("message.updated")
MESSAGE_REMOVED = EventDef("message.removed")
PART_UPDATED = EventDef("part.updated")
PART_REMOVED = EventDef("part.removed")
PART_DELTA = EventDef("part.delta")

# --- Permission Events ---
PERMISSION_ASKED = EventDef("permission.asked")
PERMISSION_REPLIED = EventDef("permission.replied")

# --- File Events ---
FILE_EDITED = EventDef("file.edited")

# --- LSP Events ---
LSP_UPDATED = EventDef("lsp.updated")

# --- MCP Events ---
MCP_TOOLS_CHANGED = EventDef("mcp.tools.changed")

# --- Instance Events ---
INSTANCE_DISPOSED = EventDef("server.instance.disposed")

# --- Project Events ---
PROJECT_UPDATED = EventDef("project.updated")
