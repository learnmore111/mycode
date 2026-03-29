"""Message data models. Equivalent to src/session/message-v2.ts."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from opencode.util import ids


@dataclass
class TextPart:
    id: str = ""
    session_id: str = ""
    message_id: str = ""
    type: Literal["text"] = "text"
    content: str = ""
    time_created: int = 0

@dataclass
class ToolPart:
    id: str = ""
    session_id: str = ""
    message_id: str = ""
    type: Literal["tool"] = "tool"
    tool: str = ""
    tool_call_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    time_created: int = 0
    time_completed: int | None = None

@dataclass
class ReasoningPart:
    id: str = ""
    session_id: str = ""
    message_id: str = ""
    type: Literal["reasoning"] = "reasoning"
    content: str = ""
    time_created: int = 0

@dataclass
class FilePart:
    id: str = ""
    session_id: str = ""
    message_id: str = ""
    type: Literal["file"] = "file"
    mime_type: str = ""
    content: str = ""  # base64
    filename: str = ""
    time_created: int = 0

Part = TextPart | ToolPart | ReasoningPart | FilePart

@dataclass
class UserMessage:
    id: str = ""
    session_id: str = ""
    role: Literal["user"] = "user"
    time_created: int = 0

@dataclass
class AssistantMessage:
    id: str = ""
    session_id: str = ""
    role: Literal["assistant"] = "assistant"
    parent_id: str | None = None
    model_id: str = ""
    provider_id: str = ""
    agent: str = ""
    variant: str | None = None
    system: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    cost: float = 0.0
    time_created: int = 0
    time_completed: int | None = None

MessageInfo = UserMessage | AssistantMessage

@dataclass
class WithParts:
    info: MessageInfo
    parts: list[Part] = field(default_factory=list)

def create_user_message(session_id: str, message_id: str | None = None) -> UserMessage:
    return UserMessage(id=message_id or ids.message_id(), session_id=session_id, time_created=int(time.time() * 1000))

def create_assistant_message(session_id: str, parent_id: str, provider_id: str, model_id: str, agent: str) -> AssistantMessage:
    return AssistantMessage(
        id=ids.message_id(), session_id=session_id, parent_id=parent_id,
        provider_id=provider_id, model_id=model_id, agent=agent,
        time_created=int(time.time() * 1000),
    )

def create_text_part(session_id: str, message_id: str) -> TextPart:
    return TextPart(id=ids.part_id(), session_id=session_id, message_id=message_id, time_created=int(time.time() * 1000))

def create_tool_part(session_id: str, message_id: str, tool: str, call_id: str) -> ToolPart:
    return ToolPart(
        id=ids.part_id(), session_id=session_id, message_id=message_id,
        tool=tool, tool_call_id=call_id, time_created=int(time.time() * 1000),
    )


# --------------- Persistence helpers ---------------

def save_message(msg: MessageInfo) -> None:
    """Persist a UserMessage or AssistantMessage to the database."""
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import MessageTable

    row = MessageTable(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        time_created=msg.time_created,
    )

    if isinstance(msg, AssistantMessage):
        row.parent_id = msg.parent_id
        row.model_id = msg.model_id
        row.provider_id = msg.provider_id
        row.agent = msg.agent
        row.variant = msg.variant
        row.system = json.dumps(msg.system) if msg.system else None
        row.error = json.dumps(msg.error) if msg.error else None
        row.tokens_input = msg.tokens_input
        row.tokens_output = msg.tokens_output
        row.tokens_reasoning = msg.tokens_reasoning
        row.tokens_cache_read = msg.tokens_cache_read
        row.tokens_cache_write = msg.tokens_cache_write
        row.cost = msg.cost
        row.time_completed = msg.time_completed

    db = get_db_session()
    try:
        db.merge(row)
        db.commit()
    finally:
        db.close()


def save_part(part: Part) -> None:
    """Persist a Part (text/tool/reasoning/file) to the database."""
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import PartTable

    row = PartTable(
        id=part.id,
        message_id=part.message_id,
        session_id=part.session_id,
        type=part.type,
        time_created=part.time_created,
    )

    if isinstance(part, TextPart):
        row.content = part.content
    elif isinstance(part, ToolPart):
        row.tool = part.tool
        row.tool_call_id = part.tool_call_id
        row.state = part.state
        row.time_completed = part.time_completed
        row.content = part.state.get("output", "")
    elif isinstance(part, ReasoningPart):
        row.content = part.content
    elif isinstance(part, FilePart):
        row.content = part.content
        row.tool = part.filename  # store filename in tool column for convenience

    db = get_db_session()
    try:
        db.merge(row)
        db.commit()
    finally:
        db.close()


def save_parts(parts: list[Part]) -> None:
    """Persist multiple parts in a single transaction."""
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import PartTable

    if not parts:
        return

    db = get_db_session()
    try:
        for part in parts:
            row = PartTable(
                id=part.id,
                message_id=part.message_id,
                session_id=part.session_id,
                type=part.type,
                time_created=part.time_created,
            )
            if isinstance(part, TextPart):
                row.content = part.content
            elif isinstance(part, ToolPart):
                row.tool = part.tool
                row.tool_call_id = part.tool_call_id
                row.state = part.state
                row.time_completed = part.time_completed
                row.content = part.state.get("output", "")
            elif isinstance(part, (ReasoningPart, FilePart)):
                row.content = part.content
            db.merge(row)
        db.commit()
    finally:
        db.close()
