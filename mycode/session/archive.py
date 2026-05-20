"""会话归档（导出 / 导入）— 整个会话的往返传输。

归档格式是一个包含三个顶级区块的 UTF-8 JSON 文档：

    {
      "format": "mycode-session-archive",
      "version": 1,
      "exported_at": <unix-ms>,
      "session":   { ...SessionTable 行，去掉 project_id（导入时会重新绑定）... },
      "messages":  [ {..row, parts: [...] }, ... ],
      "compaction_events": [ ... ],
    }

设计选择：

- **JSON 而非 JSONL。** 单个数据块更容易签名 / 加密 / 比对；
  消息足够小，典型会话保持在几 MB 以内。
- **暂不支持二进制附件。** 多模态文件片段（图片、PDF、
  音频）以 base64 载荷的形式包含在 ``type=file`` 片段的 ``content`` 字段中 —
  无需特殊提取。
- **不包含 snapshot-git 内容。** 影子 git 提交通过哈希引用
  （``message.snapshot_ref``），但我们不打包 blob 树本身；
  需要完全可复现性的用户应将归档与
  ``~/.local/share/mycode/snapshot/`` 下的快照 git 仓库配对使用。
"""

from __future__ import annotations

import json
import time
from typing import Any

from mycode.project.instance import current_or_none
from mycode.session.message import get_compaction_events
from mycode.session.session import SessionInfo, _to_row_dict
from mycode.session.session import get as get_session
from mycode.storage.database import get_session as get_db_session
from mycode.storage.models import MessageTable, PartTable, SessionTable
from mycode.util import ids
from mycode.util import log as logmod

logger = logmod.create(service="session.archive")

ARCHIVE_FORMAT = "mycode-session-archive"
ARCHIVE_VERSION = 1


def _message_to_dict(row: MessageTable) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "role": row.role,
        "parent_id": row.parent_id,
        "turn_number": row.turn_number,
        "snapshot_ref": row.snapshot_ref,
        "model_id": row.model_id,
        "provider_id": row.provider_id,
        "agent": row.agent,
        "variant": row.variant,
        "system": row.system,
        "error": row.error,
        "tokens_input": row.tokens_input,
        "tokens_output": row.tokens_output,
        "tokens_reasoning": row.tokens_reasoning,
        "tokens_cache_read": row.tokens_cache_read,
        "tokens_cache_write": row.tokens_cache_write,
        "cost": row.cost,
        "time_created": row.time_created,
        "time_completed": row.time_completed,
    }


def _part_to_dict(row: PartTable) -> dict[str, Any]:
    return {
        "id": row.id,
        "message_id": row.message_id,
        "session_id": row.session_id,
        "type": row.type,
        "content": row.content,
        "tool": row.tool,
        "tool_call_id": row.tool_call_id,
        "state": row.state,  # already JSON-decoded via model property
        "time_created": row.time_created,
        "time_completed": row.time_completed,
    }


def export_session(session_id: str) -> dict[str, Any]:
    """将会话序列化为归档字典。如果不存在则抛出 KeyError。"""
    info = get_session(session_id)

    db = get_db_session()
    try:
        message_rows = (
            db.query(MessageTable)
            .filter(MessageTable.session_id == session_id)
            .order_by(MessageTable.time_created)
            .all()
        )
        message_ids = [m.id for m in message_rows]
        parts_by_msg: dict[str, list[dict[str, Any]]] = {}
        if message_ids:
            for p in (
                db.query(PartTable)
                .filter(PartTable.message_id.in_(message_ids))
                .order_by(PartTable.time_created)
                .all()
            ):
                parts_by_msg.setdefault(p.message_id, []).append(_part_to_dict(p))
    finally:
        db.close()

    messages = []
    for m in message_rows:
        payload = _message_to_dict(m)
        payload["parts"] = parts_by_msg.get(m.id, [])
        messages.append(payload)

    compaction_events: list[dict[str, Any]]
    try:
        compaction_events = get_compaction_events(session_id)
    except Exception as exc:
        logger.warn("compaction event export failed, skipping", error=str(exc))
        compaction_events = []

    session_payload = _to_row_dict(info)
    # project_id 在导入时重新绑定 — 删除它以保持归档的可移植性。
    session_payload.pop("project_id", None)

    return {
        "format": ARCHIVE_FORMAT,
        "version": ARCHIVE_VERSION,
        "exported_at": int(time.time() * 1000),
        "session": session_payload,
        "messages": messages,
        "compaction_events": compaction_events,
    }


def export_session_json(session_id: str, *, indent: int | None = 2) -> str:
    """便捷方法：序列化为 JSON 字符串（CLI 友好）。"""
    return json.dumps(export_session(session_id), ensure_ascii=False, indent=indent)


def _require(archive: dict[str, Any]) -> None:
    fmt = archive.get("format")
    ver = archive.get("version")
    if fmt != ARCHIVE_FORMAT:
        raise ValueError(f"Unsupported archive format: {fmt!r}")
    if ver != ARCHIVE_VERSION:
        raise ValueError(f"Unsupported archive version: {ver!r}")
    if not isinstance(archive.get("session"), dict):
        raise ValueError("Archive missing `session` block")
    if not isinstance(archive.get("messages"), list):
        raise ValueError("Archive missing `messages` block")


def import_session(
    archive: dict[str, Any],
    *,
    new_id: bool = True,
    title_prefix: str = "",
) -> SessionInfo:
    """将归档导入当前项目上下文。

    参数:
        archive: 由 ``export_session`` 生成的解析后字典。
        new_id: 为 True 时（默认）分配一个新的会话 ID 并重写
            每个消息/片段以引用它。设为 False 则保留原始 ID —
            仅当调用方已确认该 ID 不存在时有用。
        title_prefix: 可选字符串，添加到恢复的标题前，
            以便在侧边栏中容易识别导入项（例如 ``"[imported] "``）。

    返回:
        写入数据库的 ``SessionInfo``。
    """
    _require(archive)

    ctx = current_or_none()
    if ctx is None:
        raise RuntimeError("import_session requires an active project instance context")

    session_in = dict(archive["session"])
    original_id = session_in.get("id")
    target_id = ids.session_id() if new_id or not original_id else original_id
    session_in["id"] = target_id
    session_in["project_id"] = ctx.project.id
    if title_prefix:
        session_in["title"] = f"{title_prefix}{session_in.get('title') or ''}".strip()
    session_in.setdefault("time_created", int(time.time() * 1000))
    session_in["time_updated"] = int(time.time() * 1000)
    session_in.setdefault("visible", 1)

    # 将旧消息 ID 映射到新 ID，以便 parent_id / 片段保持一致。
    id_map: dict[str, str] = {}
    for m in archive["messages"]:
        old = m.get("id")
        id_map[old] = ids.message_id() if new_id else old

    # 在单个事务中持久化所有内容。
    db = get_db_session()
    try:
        # Refresh create_session_row 副作用需要上下文，但我们直接组装行，
        # 以便导入是一次性提交。
        row = SessionTable(**{k: v for k, v in session_in.items() if v is not None})
        # summary 列（additions/deletions/files/diffs）在数据库中以扁平形式存储，
        # 但我们通过 _to_row_dict 导出时已拆分它们；行中已包含这些列。
        db.merge(row)

        for m in archive["messages"]:
            new_msg_id = id_map.get(m.get("id"), ids.message_id())
            parts = m.pop("parts", []) or []
            msg_cols = {k: v for k, v in m.items() if k not in ("parts",)}
            msg_cols["id"] = new_msg_id
            msg_cols["session_id"] = target_id
            # Rewrite parent_id if it points at another imported message.
            if msg_cols.get("parent_id") in id_map:
                msg_cols["parent_id"] = id_map[msg_cols["parent_id"]]
            db.merge(MessageTable(**{k: v for k, v in msg_cols.items() if k in MessageTable.__table__.columns}))

            for p in parts:
                new_part_id = ids.part_id() if new_id else p.get("id")
                part_cols = dict(p)
                part_cols["id"] = new_part_id
                part_cols["message_id"] = new_msg_id
                part_cols["session_id"] = target_id
                # `state` 是一个字典 — PartTable 属性 setter 期望 dict 并写入 JSON。
                # 使用行对象以便 setter 生效。
                part_row = PartTable(
                    id=part_cols["id"],
                    message_id=part_cols["message_id"],
                    session_id=part_cols["session_id"],
                    type=part_cols.get("type") or "text",
                    content=part_cols.get("content"),
                    tool=part_cols.get("tool"),
                    tool_call_id=part_cols.get("tool_call_id"),
                    time_created=part_cols.get("time_created") or int(time.time() * 1000),
                    time_completed=part_cols.get("time_completed"),
                )
                part_row.state = part_cols.get("state")
                db.merge(part_row)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 重新读取，以便返回的 SessionInfo 反映 SQLAlchemy 应用的任何规范化。
    return get_session(target_id)


def import_session_json(payload: str, **kwargs: Any) -> SessionInfo:
    """便捷方法：解析 JSON 字符串并导入。"""
    archive = json.loads(payload)
    if not isinstance(archive, dict):
        raise ValueError("Archive must be a JSON object at the top level")
    return import_session(archive, **kwargs)


def fork_session(session_id: str, turn: int, *, title: str | None = None) -> SessionInfo:
    """创建一个在 ``session_id`` 的 ``turn`` 之后分叉的新会话。

    新会话共享祖先的消息直到并包括给定的助手回合，然后分叉。
    这与回滚不同：源会话保持不变，新会话通过
    ``SessionInfo.parent_id`` + 其最后一条助手消息上保留的 ``snapshot_ref``
    记录其血统。

    参数:
        session_id: 要分叉的会话。
        turn: 包含性上限；``turn_number > turn`` 的消息不会被复制。
        title: 可选的新标题。默认为 ``"<original> (fork @turn{N})"``。

    返回:
        新创建的 ``SessionInfo``。
    """
    if turn < 1:
        raise ValueError(f"turn must be >= 1 (got {turn})")

    parent = get_session(session_id)
    archive = export_session(session_id)

    # 删除截止回合之后的任何消息（及其片段）。用户消息
    # 通过与助手消息的接近程度来识别 — 保留所有
    # time_created <= 截止助手消息 time_created 的消息。
    messages = archive.get("messages") or []
    cutoff_time: int | None = None
    for m in messages:
        if m.get("role") == "assistant" and m.get("turn_number") == turn:
            cutoff_time = m.get("time_created")
            break
    if cutoff_time is None:
        raise KeyError(f"session {session_id} has no assistant turn {turn}")

    pruned = [m for m in messages if (m.get("time_created") or 0) <= cutoff_time]
    archive = {**archive, "messages": pruned}

    # 重新命名并删除任何归档摘要，以便分叉从头开始。
    sess_payload = dict(archive["session"])
    sess_payload["parent_id"] = parent.id
    sess_payload["title"] = title or f"{parent.title} (fork @turn{turn})"
    # 重置 summary/diff 聚合 — 它们描述的是父会话的状态。
    for key in ("summary_additions", "summary_deletions", "summary_files", "summary_diffs", "revert"):
        sess_payload[key] = None
    archive["session"] = sess_payload

    return import_session(archive, new_id=True)


__all__ = [
    "ARCHIVE_FORMAT",
    "ARCHIVE_VERSION",
    "export_session",
    "export_session_json",
    "fork_session",
    "import_session",
    "import_session_json",
]
