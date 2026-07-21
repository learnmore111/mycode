"""Idle-session shadow extraction into the versioned memory inbox."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.exc import IntegrityError

from mycode.config import config as configmod
from mycode.session.memory.service import MemoryRejectedError, MemoryService, is_explicit_remember
from mycode.storage.database import use
from mycode.storage.models import (
    MemoryExtractionStateTable,
    MessageTable,
    PartTable,
    SessionTable,
)
from mycode.util import log as logmod

logger = logmod.create(service="session.memory.background")
EXTRACTOR_VERSION = "rules-v2"
_RUN_LOCK = threading.Lock()
_EXTERNAL_TOOLS = frozenset({"webfetch", "websearch", "task", "subagent"})

_PREFERENCE_PATTERNS = (
    re.compile(r"(?:i|we)\s+(?:prefer|want|need|always use)\s+(.{8,240})", re.IGNORECASE),
    re.compile(r"(?:please\s+)?(?:always|never|do not|don't)\s+(.{8,240})", re.IGNORECASE),
    re.compile(r"(?:我偏好|我希望|以后请|请始终|请不要|不要)。{0,2}(.{4,160})"),
)
_CORRECTION_PATTERNS = (
    re.compile(r"(?:that's wrong|not .{1,80},? (?:but|use)|instead[, :]?)(.{6,220})", re.IGNORECASE),
    re.compile(r"(?:不是.{1,80}而是|应该是|改成)(.{4,160})"),
)
_PROJECT_FACT_PATTERNS = (
    re.compile(r"(?:this|the|our)\s+(?:project|repository|repo)\s+(?:uses|is|has|requires)\s+(.{8,220})", re.IGNORECASE),
    re.compile(r"(?:这个|我们的)?(?:项目|仓库)(?:使用|是|有|需要)(.{4,160})"),
)
_PROCEDURE_PATTERNS = (
    re.compile(r"(?:when|whenever)\s+(.{4,100}),?\s+(?:first|always|then)\s+(.{6,160})", re.IGNORECASE),
    re.compile(r"(?:遇到|当)(.{2,80})(?:时|的时候)[,，]?(?:先|需要|应该)(.{4,120})"),
)
_EXPERIENCE_PATTERNS = (
    re.compile(r"(?:last time|previously).{0,60}(?:failed|broke|did not work)[: ,]?(.{6,180})", re.IGNORECASE),
    re.compile(r"(?:上次|之前).{0,40}(?:失败|不行|出错)[：:，,]?(.{4,140})"),
)
_REFERENCE_PATTERN = re.compile(r"https?://[^\s)\]}>。，,]+", re.IGNORECASE)


@dataclass(frozen=True)
class CandidateSpec:
    subject: str
    content: str
    memory_type: str
    trigger_description: str
    source_message_id: str
    confidence: float
    explicit: bool


def extract_candidate_specs(messages: list[tuple[str, str]]) -> list[CandidateSpec]:
    """High-precision multilingual rule baseline for shadow extraction."""
    candidates: list[CandidateSpec] = []
    seen: set[str] = set()
    for message_id, content in messages:
        clean = " ".join(content.split())
        if not clean or "<system-reminder>" in clean:
            continue
        explicit = is_explicit_remember(clean)
        typed_matches: list[tuple[str, str]] = []
        for pattern in _PREFERENCE_PATTERNS:
            typed_matches.extend((str(match).strip(" .,:;"), "user_preference") for match in pattern.findall(clean))
        for pattern in _CORRECTION_PATTERNS:
            typed_matches.extend((str(match).strip(" .,:;"), "feedback") for match in pattern.findall(clean))
        for pattern in _PROJECT_FACT_PATTERNS:
            typed_matches.extend((str(match).strip(" .,:;"), "project_fact") for match in pattern.findall(clean))
        for pattern in _PROCEDURE_PATTERNS:
            for match in pattern.findall(clean):
                value = " -> ".join(match) if isinstance(match, tuple) else str(match)
                typed_matches.append((value.strip(" .,:;"), "procedure_candidate"))
        for pattern in _EXPERIENCE_PATTERNS:
            typed_matches.extend((str(match).strip(" .,:;"), "episodic_experience") for match in pattern.findall(clean))
        typed_matches.extend((url, "reference") for url in _REFERENCE_PATTERN.findall(clean))
        if explicit and not typed_matches:
            typed_matches.append((clean[:240], "user_preference"))
        for matched, memory_type in typed_matches:
            if len(matched) < 4:
                continue
            digest = hashlib.sha256(matched.casefold().encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            subject = _subject(matched)
            candidates.append(CandidateSpec(
                subject=subject,
                content=matched,
                memory_type=memory_type,
                trigger_description=f"Apply when a future task relates to: {subject}",
                source_message_id=message_id,
                confidence=0.95 if explicit else 0.85,
                explicit=explicit,
            ))
            if len(candidates) >= 5:
                return candidates
    return candidates


def run_eligible_extractions(
    project_path: str,
    project_id: str,
    *,
    exclude_session_id: str | None = None,
) -> dict[str, int]:
    """Claim and process eligible idle sessions without touching the foreground path."""
    cfg = configmod.get().memory
    if not cfg or not cfg.enabled or not cfg.generate_memories:
        return {"sessions": 0, "candidates": 0, "active": 0, "skipped_external": 0}
    if not _RUN_LOCK.acquire(blocking=False):
        return {"sessions": 0, "candidates": 0, "active": 0, "skipped_external": 0}
    try:
        now = int(time.time() * 1000)
        idle_before = now - int(cfg.idle_minutes) * 60_000
        sessions = use(
            lambda db: db.query(SessionTable)
            .filter(
                SessionTable.project_id == project_id,
                SessionTable.time_updated <= idle_before,
                SessionTable.time_archived.is_(None),
            )
            .order_by(SessionTable.time_updated)
            .limit(20)
            .all()
        )
        result = {"sessions": 0, "candidates": 0, "active": 0, "skipped_external": 0}
        service = MemoryService(project_path, project_id=project_id)
        for session in sessions:
            if session.id == exclude_session_id:
                continue
            from mycode.session.prompt import is_session_busy

            if is_session_busy(session.id):
                continue
            payload = _session_payload(session.id)
            if len(payload["messages"]) < int(cfg.min_user_prompts):
                continue
            version = _version(payload)
            if not _claim(session.id, version, now):
                continue
            if cfg.disable_on_external_context and payload["has_external"]:
                _complete(session.id, version, 0, "skipped_external")
                result["skipped_external"] += 1
                continue
            try:
                specs = extract_candidate_specs(payload["messages"])
                created = 0
                active = 0
                for spec in specs:
                    try:
                        kwargs: dict[str, Any] = {
                            "subject": spec.subject,
                            "content": spec.content,
                            "trigger_description": spec.trigger_description,
                            "memory_type": spec.memory_type,
                            "scope_type": (
                                "user" if spec.memory_type in {"user_preference", "feedback"} else "project"
                            ),
                            "source_session_id": session.id,
                            "source_message_ids": [spec.source_message_id],
                            "source_kind": "user_statement",
                            "confidence": spec.confidence,
                            "extractor_version": EXTRACTOR_VERSION,
                            "created_by": "background_extractor",
                        }
                        if spec.explicit:
                            record = service.remember(**kwargs)
                        else:
                            record = service.create(**kwargs, status="pending")
                        belongs_to_this_run = (
                            record.source_session_id == session.id
                            and record.extractor_version == EXTRACTOR_VERSION
                            and record.created_by == "background_extractor"
                        )
                        if not belongs_to_this_run:
                            continue
                        created += 1
                        if spec.explicit and record.status == "active":
                            active += 1
                    except MemoryRejectedError:
                        logger.info("candidate rejected by memory safety scan", session_id=session.id)
                _complete(session.id, version, created, "completed")
                result["sessions"] += 1
                result["candidates"] += created - active
                result["active"] += active
            except Exception as exc:
                _complete(session.id, version, 0, "failed", str(exc))
                logger.warn("background memory extraction failed", session_id=session.id, error=str(exc))
        return result
    finally:
        _RUN_LOCK.release()


def _session_payload(session_id: str) -> dict[str, Any]:
    def _load(db: Any) -> dict[str, Any]:
        user_rows = (
            db.query(MessageTable)
            .filter(MessageTable.session_id == session_id, MessageTable.role == "user")
            .order_by(MessageTable.time_created)
            .all()
        )
        message_ids = [row.id for row in user_rows]
        text_rows = (
            db.query(PartTable)
            .filter(PartTable.message_id.in_(message_ids), PartTable.type == "text")
            .order_by(PartTable.time_created)
            .all()
            if message_ids else []
        )
        text_by_message: dict[str, str] = {}
        for row in text_rows:
            text_by_message[row.message_id] = text_by_message.get(row.message_id, "") + (row.content or "")
        tools = db.query(PartTable.tool).filter(PartTable.session_id == session_id, PartTable.type == "tool").all()
        tool_names = {str(row[0] or "").casefold() for row in tools}
        has_external = any(
            tool in _EXTERNAL_TOOLS or tool.startswith(("mcp_", "mcp__", "web")) for tool in tool_names
        )
        return {
            "messages": [(row.id, text_by_message.get(row.id, "")) for row in user_rows if text_by_message.get(row.id)],
            "has_external": has_external,
            "last_time": max((row.time_created for row in user_rows), default=0),
        }

    return cast("dict[str, Any]", use(_load))


def _version(payload: dict[str, Any]) -> str:
    serialized = "\n".join(f"{message_id}:{content}" for message_id, content in payload["messages"])
    return f"{EXTRACTOR_VERSION}:{hashlib.sha256(serialized.encode()).hexdigest()}"


def _claim(session_id: str, version: str, now: int) -> bool:
    def _update(db: Any) -> bool:
        row = db.get(MemoryExtractionStateTable, session_id)
        if row and row.processed_version == version:
            if row.status in {"completed", "skipped_external"}:
                return False
            if row.status == "running" and row.time_started > now - 30 * 60_000:
                return False
        if row is None:
            row = MemoryExtractionStateTable(
                session_id=session_id,
                processed_version=version,
                status="running",
                candidate_count=0,
                error=None,
                time_started=now,
                time_completed=None,
            )
            db.add(row)
        else:
            row.processed_version = version
            row.status = "running"
            row.candidate_count = 0
            row.error = None
            row.time_started = now
            row.time_completed = None
        return True

    try:
        return bool(use(_update))
    except IntegrityError:
        # Another process claimed this session between SELECT and INSERT.
        return False


def _complete(session_id: str, version: str, count: int, status: str, error: str | None = None) -> None:
    def _update(db: Any) -> None:
        row = db.get(MemoryExtractionStateTable, session_id)
        if row and row.processed_version == version:
            row.status = status
            row.candidate_count = count
            row.error = error
            row.time_completed = int(time.time() * 1000)

    use(_update)


def _subject(content: str) -> str:
    compact = " ".join(content.split()).strip()
    if len(compact) <= 60:
        return compact
    return compact[:57].rstrip() + "..."


__all__ = ["CandidateSpec", "EXTRACTOR_VERSION", "extract_candidate_specs", "run_eligible_extractions"]
