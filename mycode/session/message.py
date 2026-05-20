"""消息数据模型。

功能：
- SystemMessage（info/warning/error/compact_boundary 子类型）
- 消息元数据：isMeta（对 UI 隐藏但发送给模型）、来源跟踪
- 消息规范化管道（normalizeMessagesForAPI）
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from mycode.util import ids

# ---------------------------------------------------------------------------
# 消息来源跟踪
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
# 片段类型
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
# 消息类型
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
    raw_usage: dict[str, Any] | None = None
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

def create_reasoning_part(session_id: str, message_id: str) -> ReasoningPart:
    return ReasoningPart(
        id=ids.part_id(),
        session_id=session_id,
        message_id=message_id,
        time_created=int(time.time() * 1000),
    )

def create_tool_part(session_id: str, message_id: str, tool: str, call_id: str) -> ToolPart:
    return ToolPart(
        id=ids.part_id(), session_id=session_id, message_id=message_id,
        tool=tool, tool_call_id=call_id, time_created=int(time.time() * 1000),
    )

def create_file_part(
    session_id: str,
    message_id: str,
    *,
    mime_type: str,
    content: str,
    filename: str = "",
) -> FilePart:
    """为图片 / PDF / 音频附件创建 :class:`FilePart`。

    ``content`` 是 base64 编码的负载（或 ``data:`` URI）。
    ``mime_type`` 例如 ``image/png``、``application/pdf``、``audio/wav``。
    ``filename`` 是可选的，存储在数据库的 ``tool`` 列中。
    """
    return FilePart(
        id=ids.part_id(),
        session_id=session_id,
        message_id=message_id,
        mime_type=mime_type,
        content=content,
        filename=filename,
        time_created=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# 消息规范化管道
# ---------------------------------------------------------------------------


def normalize_messages_for_api(
    messages: list[dict[str, Any]],
    *,
    include_system: bool = False,
) -> list[dict[str, Any]]:
    """规范化消息以发送给 LLM API。

    执行：
    1. 过滤掉 subtype='local_command' 的 SystemMessage（从不发送给 API）
    2. 过滤掉 compact_boundary 标记（保留，仅供内部状态使用）
    3. 可选地将系统消息作为用户上下文包含
    4. 确保消息格式与 API 兼容
    """
    normalized: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")

        # 跳过 local_command 系统消息（例如 /compact 输出）
        if role == "system" and msg.get("subtype") == "local_command":
            continue

        # 跳过 compact_boundary 标记（保留供将来使用）
        if role == "system" and msg.get("subtype") == "compact_boundary":
            continue

        # 如果请求，将系统 info/warning/error 转换为用户可见的上下文
        if role == "system" and not include_system:
            subtype = msg.get("subtype", "info")
            if subtype in ("info", "warning", "error"):
                content = msg.get("content", "")
                if content:
                    normalized.append({"role": "user", "content": f"[System {subtype}] {content}"})
            continue

        normalized.append(msg)
    return normalized


# --------------- 持久化辅助函数 ---------------

def save_message(msg: MessageInfo) -> None:
    """将 UserMessage 或 AssistantMessage 持久化到数据库。"""
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable

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
    """将 Part（text/tool/reasoning/file）持久化到数据库。"""
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import PartTable

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
        row.tool = part.filename  # store filename in tool column
        row.tool_call_id = part.mime_type  # store mime_type in tool_call_id column

    db = get_db_session()
    try:
        db.merge(row)
        db.commit()
    finally:
        db.close()


def save_parts(parts: list[Part]) -> None:
    """在单个事务中持久化多个片段。"""
    from mycode.storage.database import get_session as get_db_session

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


def _build_part_row(part: Part) -> Any:
    """从 Part 对象构建 PartTable 行。"""
    from mycode.storage.models import PartTable

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
        row.tool_call_id = part.mime_type
    return row


def _build_message_row(msg: MessageInfo) -> Any:
    """从 MessageInfo 对象构建 MessageTable 行。"""
    from mycode.storage.models import MessageTable

    row = MessageTable(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        time_created=msg.time_created,
    )
    # 回合号 + 快照引用是回滚 API 依赖的可选元数据。
    # 它们可能通过 ``setattr`` 从编排器附加到 MessageInfo；
    # 对旧版调用者回退到 None 是安全的。
    row.turn_number = getattr(msg, "turn_number", None)
    row.snapshot_ref = getattr(msg, "snapshot_ref", None)
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
        row.raw_usage = json.dumps(msg.raw_usage, ensure_ascii=False) if msg.raw_usage else None
        row.time_completed = msg.time_completed
    return row


def persist_turn(session_id: str, msg: MessageInfo, parts: list[Part]) -> None:
    """原子性地持久化完整回合：消息 + 所有片段 + 会话更新。

    所有写入发生在单个数据库事务中。要么全部提交，要么什么都不提交 —
    数据库中没有部分状态。
    """
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import SessionTable

    db = get_db_session()
    try:
        # 1. 消息
        msg_row = _build_message_row(msg)
        db.merge(msg_row)

        # 2. 所有片段
        for part in parts:
            part_row = _build_part_row(part)
            db.merge(part_row)

        # 3. 更新会话时间戳
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


def next_turn_number(session_id: str) -> int:
    """返回会话的下一个回合号。

    回合号每次助手响应增加 1。如果会话没有先前的助手消息，
    第一回合为 ``1``。
    """
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable

    db = get_db_session()
    try:
        row = (
            db.query(MessageTable.turn_number)
            .filter(
                MessageTable.session_id == session_id,
                MessageTable.role == "assistant",
                MessageTable.turn_number.isnot(None),
            )
            .order_by(MessageTable.turn_number.desc())
            .first()
        )
        if row and row[0]:
            return int(row[0]) + 1
        return 1
    finally:
        db.close()


def rollback_to_turn(session_id: str, turn_number: int) -> dict[str, Any]:
    """删除所有 ``turn_number > turn`` 的消息（及其片段）。

    同时返回保留回合处记录的快照引用（如果有），以便调用方可以
    同时恢复文件系统和对话记录。

    返回 ``{"kept": <count>, "removed": <count>, "snapshot_ref": str | None}``。
    如果请求的回合在会话中不存在，则抛出 ``KeyError``。
    """
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable, PartTable

    if turn_number < 0:
        raise ValueError(f"turn_number must be >= 0, got {turn_number}")

    db = get_db_session()
    try:
        target = (
            db.query(MessageTable)
            .filter(
                MessageTable.session_id == session_id,
                MessageTable.role == "assistant",
                MessageTable.turn_number == turn_number,
            )
            .one_or_none()
        )
        if target is None and turn_number != 0:
            raise KeyError(f"No assistant turn {turn_number} in session {session_id}")

        # 收集目标回合之后的消息 ID，以便级联删除它们的片段。
        # 用户消息的 ``turn_number is None`` — 我们删除那些 time_created
        # 在保留助手回合之后的消息。回滚到回合 0 时，保留的时间是
        # 会话创建时间（所有用户级别内容都被清除）。
        kept_time = target.time_created if target else 0

        to_delete_ids = [
            row.id
            for row in db.query(MessageTable.id, MessageTable.time_created, MessageTable.turn_number)
            .filter(MessageTable.session_id == session_id)
            .all()
            if (
                (row.turn_number is not None and row.turn_number > turn_number)
                or (row.turn_number is None and row.time_created > kept_time)
            )
        ]

        removed = 0
        if to_delete_ids:
            db.query(PartTable).filter(PartTable.message_id.in_(to_delete_ids)).delete(
                synchronize_session=False,
            )
            removed = (
                db.query(MessageTable)
                .filter(MessageTable.id.in_(to_delete_ids))
                .delete(synchronize_session=False)
            )

        kept = (
            db.query(MessageTable)
            .filter(MessageTable.session_id == session_id)
            .count()
        )
        db.commit()
        snapshot_ref = target.snapshot_ref if target else None
        return {"kept": kept, "removed": removed, "snapshot_ref": snapshot_ref}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_last_assistant_time(session_id: str) -> int | None:
    """返回会话中最后一条助手消息的 time_completed（纪元毫秒）。

    如果不存在已完成的助手消息，则返回 None。
    """
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable

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
    """从数据库重建 OpenAI 格式的对话历史。

    加载会话的所有消息和片段，并将它们转换为
    ``prompt(history=...)`` 期望的 ``[{"role": ..., "content": ...}, ...]`` 格式。

    跳过系统消息（它们在运行时被 prompt.py 重新注入）。

    如果会话尚无消息，则返回空列表。
    """
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import MessageTable, PartTable

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

        # 按 message_id 分组片段
        parts_by_msg: dict[str, list[Any]] = {}
        for p in parts_rows:
            parts_by_msg.setdefault(p.message_id, []).append(p)

        import re as _re

        reminder_re = _re.compile(r"<system-reminder>.*?</system-reminder>", _re.DOTALL)

        def _append_reminder_to_last_user(reminder_text: str) -> bool:
            """将持久化的纯提醒消息合并回上一个用户回合。"""
            for entry in reversed(result):
                if entry.get("role") != "user":
                    continue
                content = entry.get("content")
                if isinstance(content, str):
                    entry["content"] = f"{content}\n\n{reminder_text}" if content else reminder_text
                    return True
                if isinstance(content, list):
                    content.append({"type": "text", "text": reminder_text})
                    return True
            return False

        result: list[dict[str, Any]] = []

        for msg in messages_rows:
            # 跳过系统消息 — 它们在运行时被重新注入
            if msg.role == "system":
                continue

            msg_parts = parts_by_msg.get(msg.id, [])
            text_parts = [p for p in msg_parts if p.type == "text"]
            tool_parts = [p for p in msg_parts if p.type == "tool"]

            if msg.role == "user":
                text_content = "".join(p.content or "" for p in text_parts)

                # 持久化的纯系统提醒元消息属于前面的用户回合。
                # 重新附加它们，以便重建的历史与发送给模型的运行时上下文匹配。
                stripped = reminder_re.sub("", text_content).strip()
                if not stripped and "<system-reminder>" in text_content:
                    _append_reminder_to_last_user(text_content)
                    continue

                file_parts = [p for p in msg_parts if p.type == "file"]
                if file_parts:
                    # 多模态用户消息 — 重建 OpenAI 内容列表
                    content_list: list[dict[str, Any]] = []
                    if text_content:
                        content_list.append({"type": "text", "text": text_content})
                    for fp in file_parts:
                        mime = fp.tool_call_id or ""  # mime_type stored here
                        raw = fp.content or ""
                        url = raw if raw.startswith(("data:", "http://", "https://")) else f"data:{mime};base64,{raw}"
                        if mime.startswith("image/"):
                            content_list.append({"type": "image_url", "image_url": {"url": url}})
                        elif mime.startswith("audio/"):
                            content_list.append({"type": "input_audio", "input_audio": {"data": url}})
                        else:
                            # pdf / generic file
                            content_list.append({"type": "file", "file": {"file_data": url}})
                    if content_list:
                        result.append({"role": "user", "content": content_list})
                elif text_content:
                    result.append({"role": "user", "content": text_content})

            elif msg.role == "assistant":
                text_content = "".join(p.content or "" for p in text_parts)

                if tool_parts:
                    # 带工具调用的助手消息 — 匹配 processor.build_tool_results_messages() 格式
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
                    # 纯文本助手消息
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
    """持久化带有压缩前上下文的压缩事件，用于审计追踪。

    存储指标和旧消息，以便稍后查看。
    这使用户能够理解压缩期间丢失了什么。
    """
    import time
    import uuid

    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import CompactionEventTable

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
    from mycode.storage.database import get_session as get_db_session
    from mycode.storage.models import CompactionEventTable

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
