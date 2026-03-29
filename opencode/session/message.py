"""Message data models. Equivalent to src/session/message-v2.ts."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal
import time
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
