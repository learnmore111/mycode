"""SQLAlchemy table definitions (SQLAlchemy 2.0 Mapped style).

Using ``Mapped[T]`` + ``mapped_column()`` so mypy infers column
attribute types correctly without extra stubs.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Boolean, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectTable(Base):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    worktree: Mapped[str] = mapped_column(String, nullable=False)
    vcs: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String, nullable=True)
    icon_color: Mapped[str | None] = mapped_column(String, nullable=True)
    time_created: Mapped[int] = mapped_column(nullable=False)
    time_updated: Mapped[int] = mapped_column(nullable=False)
    time_initialized: Mapped[int | None] = mapped_column(nullable=True)
    _sandboxes: Mapped[str] = mapped_column("sandboxes", Text, nullable=False, default="[]")
    _commands: Mapped[str | None] = mapped_column("commands", Text, nullable=True)

    @property
    def sandboxes(self) -> list[str]:
        try:
            return json.loads(self._sandboxes) if self._sandboxes else []
        except (json.JSONDecodeError, TypeError):
            return []

    @sandboxes.setter
    def sandboxes(self, value: list[str]) -> None:
        self._sandboxes = json.dumps(value)

    @property
    def commands(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._commands) if self._commands else None
        except (json.JSONDecodeError, TypeError):
            return None

    @commands.setter
    def commands(self, value: dict[str, Any] | None) -> None:
        self._commands = json.dumps(value) if value else None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionTable(Base):
    __tablename__ = "session"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    directory: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    share_url: Mapped[str | None] = mapped_column(String, nullable=True)
    revert: Mapped[str | None] = mapped_column(Text, nullable=True)
    permission: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_additions: Mapped[int | None] = mapped_column(nullable=True)
    summary_deletions: Mapped[int | None] = mapped_column(nullable=True)
    summary_files: Mapped[int | None] = mapped_column(nullable=True)
    summary_diffs: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_created: Mapped[int] = mapped_column(nullable=False)
    time_updated: Mapped[int] = mapped_column(nullable=False, index=True)
    time_compacting: Mapped[int | None] = mapped_column(nullable=True)
    time_archived: Mapped[int | None] = mapped_column(nullable=True)
    visible: Mapped[int] = mapped_column(nullable=False, server_default="1", default=1)


# ---------------------------------------------------------------------------
# Session Pause
# ---------------------------------------------------------------------------


class SessionPauseTable(Base):
    __tablename__ = "session_pause"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_user_text: Mapped[str] = mapped_column(Text, nullable=False)
    partial_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    time_paused: Mapped[int] = mapped_column(nullable=False)
    time_updated: Mapped[int] = mapped_column(nullable=False, index=True)


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class MessageTable(Base):
    __tablename__ = "message"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    turn_number: Mapped[int | None] = mapped_column(nullable=True, index=True)
    snapshot_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)
    agent: Mapped[str | None] = mapped_column(String, nullable=True)
    variant: Mapped[str | None] = mapped_column(String, nullable=True)
    system: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(nullable=True)
    tokens_reasoning: Mapped[int | None] = mapped_column(nullable=True)
    tokens_cache_read: Mapped[int | None] = mapped_column(nullable=True)
    tokens_cache_write: Mapped[int | None] = mapped_column(nullable=True)
    cost: Mapped[float | None] = mapped_column(nullable=True)
    raw_usage: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_created: Mapped[int] = mapped_column(nullable=False)
    time_completed: Mapped[int | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_message_session_created", "session_id", "time_created"),
        Index("ix_message_session_turn", "session_id", "turn_number"),
    )


# ---------------------------------------------------------------------------
# Part
# ---------------------------------------------------------------------------


class PartTable(Base):
    __tablename__ = "part"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    message_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    _state: Mapped[str | None] = mapped_column("state", Text, nullable=True)
    time_created: Mapped[int] = mapped_column(nullable=False)
    time_completed: Mapped[int | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "tool_call_id", name="uq_part_session_tool_call"),
        Index("ix_part_message_created", "message_id", "time_created"),
    )

    @property
    def state(self) -> dict[str, Any] | None:
        try:
            return json.loads(self._state) if self._state else None
        except (json.JSONDecodeError, TypeError):
            return None

    @state.setter
    def state(self, value: dict[str, Any] | None) -> None:
        self._state = json.dumps(value) if value else None


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------


class PermissionTable(Base):
    __tablename__ = "permission"

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    _data: Mapped[str] = mapped_column("data", Text, nullable=False, default="[]")

    @property
    def data(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._data) if self._data else []
        except (json.JSONDecodeError, TypeError):
            return []

    @data.setter
    def data(self, value: list[dict[str, Any]]) -> None:
        self._data = json.dumps(value)


# ---------------------------------------------------------------------------
# Compaction Event
# ---------------------------------------------------------------------------


class CompactionEventTable(Base):
    """Tracks context compaction events and metrics."""

    __tablename__ = "compaction_event"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    iteration: Mapped[int] = mapped_column(nullable=False)
    old_message_count: Mapped[int] = mapped_column(nullable=False)
    old_message_tokens: Mapped[int] = mapped_column(nullable=False)
    summary_length: Mapped[int] = mapped_column(nullable=False)
    removed_turn_count: Mapped[int] = mapped_column(nullable=False)
    _old_messages: Mapped[str] = mapped_column("old_messages", Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    time_created: Mapped[int] = mapped_column(nullable=False)

    @property
    def old_messages(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._old_messages) if self._old_messages else []
        except (json.JSONDecodeError, TypeError):
            return []

    @old_messages.setter
    def old_messages(self, value: list[dict[str, Any]]) -> None:
        self._old_messages = json.dumps(value)


# ---------------------------------------------------------------------------
# Orchestration Run
# ---------------------------------------------------------------------------


class OrchestrationRunTable(Base):
    __tablename__ = "orchestration_run"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    flow: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    directory: Mapped[str | None] = mapped_column(String, nullable=True)
    task_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    _vars: Mapped[str] = mapped_column("vars", Text, nullable=False, default="{}", server_default="{}")
    max_turns: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    walltime_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    status: Mapped[str] = mapped_column(String, nullable=False, default="running", server_default="running")
    started_at: Mapped[float] = mapped_column(Float, nullable=False)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    _result: Mapped[str | None] = mapped_column("result", Text, nullable=True)

    __table_args__ = (
        Index("ix_orchestration_run_started_at", "started_at"),
        Index("ix_orchestration_run_status_started", "status", "started_at"),
    )

    @property  # noqa: A003
    def vars(self) -> dict[str, str]:
        try:
            data = json.loads(self._vars) if self._vars else {}
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @vars.setter  # noqa: A003
    def vars(self, value: dict[str, str]) -> None:
        self._vars = json.dumps(value or {})

    @property
    def result(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._result) if self._result else None
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

    @result.setter
    def result(self, value: dict[str, Any] | None) -> None:
        self._result = json.dumps(value) if value else None
