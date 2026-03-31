"""Session management — the core agentic loop."""
from opencode.session.message import (
    AssistantMessage,
    MessageOrigin,
    Part,
    SystemMessage,
    TextPart,
    ToolPart,
    UserMessage,
    get_messages_after_compact_boundary,
    normalize_messages_for_api,
)
from opencode.session.session import SessionInfo

__all__ = [
    "AssistantMessage",
    "MessageOrigin",
    "Part",
    "SessionInfo",
    "SystemMessage",
    "TextPart",
    "ToolPart",
    "UserMessage",
    "get_messages_after_compact_boundary",
    "normalize_messages_for_api",
]
