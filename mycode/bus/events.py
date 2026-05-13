"""Event definitions for the bus system.

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

# --- Orchestration Events (M7) ---
# Every payload carries ``run_id`` plus ``flow`` (flow name) so multiple
# concurrent orchestration runs can multiplex on the same bus.  Stage
# events additionally carry ``stage_id``; spawn events carry
# ``spawn_index`` / ``agent``; swarm message events carry ``seq`` /
# ``sender`` / ``recipient`` / ``kind``.
ORCHESTRATION_FLOW_STARTED = EventDef("orchestration.flow.started")
ORCHESTRATION_FLOW_FINISHED = EventDef("orchestration.flow.finished")
ORCHESTRATION_STAGE_STARTED = EventDef("orchestration.stage.started")
ORCHESTRATION_STAGE_FINISHED = EventDef("orchestration.stage.finished")
ORCHESTRATION_SPAWN_STARTED = EventDef("orchestration.spawn.started")
ORCHESTRATION_SPAWN_FINISHED = EventDef("orchestration.spawn.finished")
ORCHESTRATION_AGENT_MESSAGE = EventDef("orchestration.agent.message")
ORCHESTRATION_AGENT_TOOL = EventDef("orchestration.agent.tool")
# Swarm mode: peer-to-peer message delivered through the mailbox.
ORCHESTRATION_MESSAGE_SENT = EventDef("orchestration.message.sent")
# Swarm mode: lifecycle events — started/finished/terminated differ from
# coordinator mode because there's no stage DAG but there is a shutdown
# negotiation reason we want visible to UIs.
ORCHESTRATION_SWARM_STARTED = EventDef("orchestration.swarm.started")
ORCHESTRATION_SWARM_FINISHED = EventDef("orchestration.swarm.finished")
