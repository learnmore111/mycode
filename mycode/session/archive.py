"""Session archive (export / import) — round-trip a whole session.

The archive format is a single UTF-8 JSON document with three top-level
sections:

    {
      "format": "mycode-session-archive",
      "version": 1,
      "exported_at": <unix-ms>,
      "session":   { ...SessionTable row, minus project_id which is rebound
                     on import... },
      "messages":  [ {..row, parts: [...] }, ... ],
      "compaction_events": [ ... ],
    }

Design choices:

- **JSON not JSONL.** Single blob is easier to sign / encrypt / diff;
  messages are small enough that a typical session stays under a few MB.
- **No binary attachments yet.** Multimodal file parts (images, PDFs,
  audio) are included as base64 payloads in the ``content`` field of
  ``type=file`` parts — no special extraction needed.
- **No snapshot-git content.** Shadow git commits are referenced by hash
  (``message.snapshot_ref``) but we do not bundle the blob tree itself;
  users who want full reproducibility should pair the archive with the
  snapshot git repo under ``~/.local/share/mycode/snapshot/``.
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
    """Serialise a session to an archive dict. Raises KeyError if absent."""
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
    # project_id is rebound on import — drop it to keep the archive portable.
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
    """Convenience: serialise to a JSON string (CLI-friendly)."""
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
    """Import an archive into the current project context.

    Args:
        archive: Parsed dict produced by ``export_session``.
        new_id: When True (default) assign a fresh session ID and rewrite
            every message/part to reference it. Leave False to preserve the
            original ID — useful only if the caller has already verified
            the ID is not already present.
        title_prefix: Optional string prepended to the restored title so
            imports are easy to spot in the sidebar (e.g. ``"[imported] "``).

    Returns:
        The ``SessionInfo`` written to the DB.
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

    # Map old message IDs → new ones so parent_id / parts stay consistent.
    id_map: dict[str, str] = {}
    for m in archive["messages"]:
        old = m.get("id")
        id_map[old] = ids.message_id() if new_id else old

    # Persist everything in a single transaction.
    db = get_db_session()
    try:
        # Refresh create_session_row side-effect requires the context, but
        # we assemble rows directly so the import is one commit.
        row = SessionTable(**{k: v for k, v in session_in.items() if v is not None})
        # summary columns (additions/deletions/files/diffs) are stored flat
        # in the DB but we exported via _to_row_dict which split them; the
        # row already has them.
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
                # `state` is a dict — the PartTable property setter expects
                # dict and writes JSON. Use a row object so the setter fires.
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

    # Re-read so the returned SessionInfo reflects whatever normalisation
    # SQLAlchemy applied.
    return get_session(target_id)


def import_session_json(payload: str, **kwargs: Any) -> SessionInfo:
    """Convenience: parse a JSON string and import it."""
    archive = json.loads(payload)
    if not isinstance(archive, dict):
        raise ValueError("Archive must be a JSON object at the top level")
    return import_session(archive, **kwargs)


def fork_session(session_id: str, turn: int, *, title: str | None = None) -> SessionInfo:
    """Create a new session that branches off after ``turn`` of ``session_id``.

    The new session shares the ancestor's messages up to and including the
    given assistant turn, then diverges. This is distinct from a rollback:
    the source session stays untouched, and the new session records its
    lineage via ``SessionInfo.parent_id`` + the preserved ``snapshot_ref``
    on its last assistant message.

    Args:
        session_id: The session to fork.
        turn: Inclusive upper bound; messages with ``turn_number > turn``
            are NOT copied.
        title: Optional new title. Defaults to ``"<original> (fork @turn{N})"``.

    Returns:
        The newly created ``SessionInfo``.
    """
    if turn < 1:
        raise ValueError(f"turn must be >= 1 (got {turn})")

    parent = get_session(session_id)
    archive = export_session(session_id)

    # Drop any message (and its parts) past the cutoff turn. User messages
    # are identified by proximity to assistants — keep everything whose
    # time_created is <= the cutoff assistant message's time_created.
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

    # Retitle & drop any archived summary so the fork starts fresh.
    sess_payload = dict(archive["session"])
    sess_payload["parent_id"] = parent.id
    sess_payload["title"] = title or f"{parent.title} (fork @turn{turn})"
    # Reset summary/diff aggregates — those describe the parent's state.
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
