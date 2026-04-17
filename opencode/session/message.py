"""Message data models.

Features:
- SystemMessage (info/warning/error/compact_boundary subtypes)
- Message metadata: isMeta (hidden from UI but sent to model), origin tracking
- Message normalization pipeline (normalizeMessagesForAPI)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from opencode.util import ids


# ---------------------------------------------------------------------------
# Message origin tracking
# ---------------------------------------------------------------------------

MessageOrigin = Literal[
    "human",           # User typed in REPL
    "api",             # SDK/API call
    "cron",            # Scheduled trigger
    "bridge",          # IDE bridge
    "teammate",        # From another agent in a team
    "system",          # System-generated (recovery, continuation)
    "proactive",       # Proactive agent suggestion
]


# ---------------------------------------------------------------------------
# Part types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


@dataclass
class UserMessage:
    id: str = ""
    session_id: str = ""
    role: Literal["user"] = "user"
    time_created: int = 0
    is_meta: bool = False      # Hidden from UI, visible to model (recovery msgs, continuations)
    origin: MessageOrigin = "human"

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
    is_api_error: bool = False  # Marks API errors (rate limit, prompt too long)
    duration_ms: int = 0        # API call duration


@dataclass
class SystemMessage:
    """System-level message for internal state tracking.

    Subtypes:
    - info:             Informational (e.g. model switch notification)
    - warning:          Warning (e.g. approaching token limit)
    - error:            Error (e.g. API failure)
    - compact_boundary: Marks where compaction occurred (messages after this are kept)
    - local_command:    Local command output (e.g. /compact result) — filtered from API
    """
    id: str = ""
    session_id: str = ""
    role: Literal["system"] = "system"
    subtype: Literal["info", "warning", "error", "compact_boundary", "local_command"] = "info"
    content: str = ""
    time_created: int = 0


MessageInfo = UserMessage | AssistantMessage | SystemMessage

@dataclass
class WithParts:
    info: MessageInfo
    parts: list[Part] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def create_user_message(
    session_id: str,
    message_id: str | None = None,
    *,
    is_meta: bool = False,
    origin: MessageOrigin = "human",
) -> UserMessage:
    return UserMessage(
        id=message_id or ids.message_id(),
        session_id=session_id,
        time_created=int(time.time() * 1000),
        is_meta=is_meta,
        origin=origin,
    )

def create_assistant_message(session_id: str, parent_id: str, provider_id: str, model_id: str, agent: str) -> AssistantMessage:
    return AssistantMessage(
        id=ids.message_id(), session_id=session_id, parent_id=parent_id,
        provider_id=provider_id, model_id=model_id, agent=agent,
        time_created=int(time.time() * 1000),
    )

def create_system_message(
    session_id: str,
    content: str,
    subtype: Literal["info", "warning", "error", "compact_boundary", "local_command"] = "info",
) -> SystemMessage:
    return SystemMessage(
        id=ids.message_id(),
        session_id=session_id,
        subtype=subtype,
        content=content,
        time_created=int(time.time() * 1000),
    )

def create_text_part(session_id: str, message_id: str) -> TextPart:
    return TextPart(id=ids.part_id(), session_id=session_id, message_id=message_id, time_created=int(time.time() * 1000))

def create_tool_part(session_id: str, message_id: str, tool: str, call_id: str) -> ToolPart:
    return ToolPart(
        id=ids.part_id(), session_id=session_id, message_id=message_id,
        tool=tool, tool_call_id=call_id, time_created=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# Message normalization pipeline
# ---------------------------------------------------------------------------


def normalize_messages_for_api(
    messages: list[dict[str, Any]],
    *,
    include_system: bool = False,
) -> list[dict[str, Any]]:
    """Normalize messages for sending to LLM API.

    Performs:
    1. Filter out SystemMessage with subtype='local_command' (never sent to API)
    2. Filter out compact_boundary markers (reserved, internal state only)
    3. Optionally include system messages as user context
    4. Ensure message format is API-compatible
    """
    normalized: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")

        # Skip local_command system messages (e.g. /compact output)
        if role == "system" and msg.get("subtype") == "local_command":
            continue

        # Skip compact_boundary markers (reserved for future use)
        if role == "system" and msg.get("subtype") == "compact_boundary":
            continue

        # Convert system info/warning/error to user-visible context if requested
        if role == "system" and not include_system:
            subtype = msg.get("subtype", "info")
            if subtype in ("info", "warning", "error"):
                content = msg.get("content", "")
                if content:
                    normalized.append({"role": "user", "content": f"[System {subtype}] {content}"})
            continue

        normalized.append(msg)
    return normalized


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
            row = _build_part_row(part)
            db.merge(row)
        db.commit()
    finally:
        db.close()


def _build_part_row(part: Part):
    """Build a PartTable row from a Part object."""
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
        row.tool = part.filename
    return row


def _build_message_row(msg: MessageInfo):
    """Build a MessageTable row from a MessageInfo object."""
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
    return row


def persist_turn(session_id: str, msg: MessageInfo, parts: list[Part]) -> None:
    """Atomically persist a complete turn: message + all parts + session touch.

    All writes happen in a single database transaction. Either everything
    is committed, or nothing is — no partial state in the DB.
    """
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import SessionTable

    db = get_db_session()
    try:
        # 1. Message
        msg_row = _build_message_row(msg)
        db.merge(msg_row)

        # 2. All parts
        for part in parts:
            part_row = _build_part_row(part)
            db.merge(part_row)

        # 3. Touch session timestamp
        session_row = db.query(SessionTable).filter(
            SessionTable.id == session_id,
        ).first()
        if session_row:
            session_row.time_updated = int(time.time() * 1000)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_last_assistant_time(session_id: str) -> int | None:
    """Return the time_completed (epoch ms) of the last assistant message in a session.

    Returns None if no completed assistant message exists.
    """
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import MessageTable

    db = get_db_session()
    try:
        row = (
            db.query(MessageTable.time_completed)
            .filter(
                MessageTable.session_id == session_id,
                MessageTable.role == "assistant",
                MessageTable.time_completed.isnot(None),
            )
            .order_by(MessageTable.time_completed.desc())
            .first()
        )
        return row[0] if row else None
    finally:
        db.close()


def rebuild_history_from_db(session_id: str) -> list[dict[str, Any]]:
    """Reconstruct OpenAI-format conversation history from the database.

    Loads all messages and parts for a session and converts them into the
    ``[{"role": ..., "content": ...}, ...]`` format expected by ``prompt(history=...)``.

    Skips system messages (they are re-injected by prompt.py at runtime).

    Returns an empty list if the session has no messages yet.
    """
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import MessageTable, PartTable

    db = get_db_session()
    try:
        messages_rows = (
            db.query(MessageTable)
            .filter(MessageTable.session_id == session_id)
            .order_by(MessageTable.time_created)
            .all()
        )
        if not messages_rows:
            return []

        message_ids = [m.id for m in messages_rows]
        parts_rows = (
            db.query(PartTable)
            .filter(PartTable.message_id.in_(message_ids))
            .order_by(PartTable.time_created)
            .all()
        )

        # Group parts by message_id
        parts_by_msg: dict[str, list] = {}
        for p in parts_rows:
            parts_by_msg.setdefault(p.message_id, []).append(p)

        result: list[dict[str, Any]] = []

        for msg in messages_rows:
            # Skip system messages — they are re-injected at runtime
            if msg.role == "system":
                continue

            msg_parts = parts_by_msg.get(msg.id, [])
            text_parts = [p for p in msg_parts if p.type == "text"]
            tool_parts = [p for p in msg_parts if p.type == "tool"]

            if msg.role == "user":
                text_content = "".join(p.content or "" for p in text_parts)
                if text_content:
                    result.append({"role": "user", "content": text_content})

            elif msg.role == "assistant":
                text_content = "".join(p.content or "" for p in text_parts)

                if tool_parts:
                    # Assistant message with tool calls — match processor.build_tool_results_messages() format
                    tool_calls_list = []
                    for tp in tool_parts:
                        state = tp.state or {}
                        tool_input = state.get("input", {})
                        tool_calls_list.append({
                            "id": tp.tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tp.tool,
                                "arguments": json.dumps(tool_input),
                            },
                        })

                    result.append({
                        "role": "assistant",
                        "content": text_content or None,
                        "tool_calls": tool_calls_list,
                    })

                    # Tool result messages
                    for tp in tool_parts:
                        state = tp.state or {}
                        output = state.get("output", "")
                        tool_message = state.get("message", "")
                        if tool_message:
                            output = f"{output}\n\n{tool_message}"
                        result.append({
                            "role": "tool",
                            "tool_call_id": tp.tool_call_id,
                            "content": output,
                        })

                elif text_content:
                    # Text-only assistant message
                    result.append({"role": "assistant", "content": text_content})

        return result
    finally:
        db.close()


def save_compaction_event(
    session_id: str,
    iteration: int,
    metrics: dict[str, int],
    old_messages: list[dict[str, Any]],
    summary: str,
) -> None:
    """Persist a compaction event with pre-compaction context for audit trail.
    
    Stores the metrics and old messages so they can be viewed later.
    This enables users to understand what was lost during compaction.
    """
    import time
    import uuid
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import CompactionEventTable
    
    event_id = f"comp-{uuid.uuid4().hex[:12]}"
    row = CompactionEventTable(
        id=event_id,
        session_id=session_id,
        iteration=iteration,
        old_message_count=metrics['old_message_count'],
        old_message_tokens=metrics['old_message_tokens'],
        summary_length=metrics['summary_length'],
        removed_turn_count=metrics['removed_turn_count'],
        old_messages=old_messages,
        summary=summary,
        time_created=int(time.time() * 1000),
    )
    
    db = get_db_session()
    try:
        db.add(row)
        db.commit()
    finally:
        db.close()


def get_compaction_events(session_id: str) -> list[dict[str, Any]]:
    """Retrieve all compaction events for a session.
    
    Returns a list of compaction events with their metrics and summaries.
    """
    from opencode.storage.database import get_session as get_db_session
    from opencode.storage.models import CompactionEventTable
    
    db = get_db_session()
    try:
        rows = db.query(CompactionEventTable).filter_by(session_id=session_id).order_by(CompactionEventTable.iteration).all()
        return [
            {
                "id": row.id,
                "session_id": row.session_id,
                "iteration": row.iteration,
                "old_message_count": row.old_message_count,
                "old_message_tokens": row.old_message_tokens,
                "summary_length": row.summary_length,
                "removed_turn_count": row.removed_turn_count,
                "old_messages": row.old_messages,
                "summary": row.summary,
                "time_created": row.time_created,
            }
            for row in rows
        ]
    finally:
        db.close()
