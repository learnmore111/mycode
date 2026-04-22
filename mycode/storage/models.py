"""SQLAlchemy table definitions."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Column, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class _JSONType(String):
    """Custom column type that serializes/deserializes JSON."""

    def __init__(self) -> None:
        super().__init__()


class ProjectTable(Base):
    __tablename__ = "project"

    id = Column(String, primary_key=True)
    worktree = Column(String, nullable=False)
    vcs = Column(String, nullable=True)  # "git" or null
    name = Column(String, nullable=True)
    icon_url = Column(String, nullable=True)
    icon_color = Column(String, nullable=True)
    time_created = Column(Integer, nullable=False)
    time_updated = Column(Integer, nullable=False)
    time_initialized = Column(Integer, nullable=True)
    # JSON-serialized list of sandbox directories
    _sandboxes = Column("sandboxes", Text, nullable=False, default="[]")
    # JSON-serialized commands object
    _commands = Column("commands", Text, nullable=True)

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


class SessionTable(Base):
    __tablename__ = "session"

    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    workspace_id = Column(String, nullable=True)
    parent_id = Column(String, nullable=True)
    slug = Column(String, nullable=False)
    directory = Column(String, nullable=False)
    title = Column(String, nullable=False)
    version = Column(String, nullable=False)
    share_url = Column(String, nullable=True)
    revert = Column(Text, nullable=True)  # JSON
    permission = Column(Text, nullable=True)  # JSON
    summary_additions = Column(Integer, nullable=True)
    summary_deletions = Column(Integer, nullable=True)
    summary_files = Column(Integer, nullable=True)
    summary_diffs = Column(Text, nullable=True)  # JSON
    time_created = Column(Integer, nullable=False)
    time_updated = Column(Integer, nullable=False, index=True)
    time_compacting = Column(Integer, nullable=True)
    time_archived = Column(Integer, nullable=True)
    visible = Column(Integer, nullable=False, server_default="1", default=1)


class SessionPauseTable(Base):
    __tablename__ = "session_pause"

    session_id = Column(String, primary_key=True)
    last_user_text = Column(Text, nullable=False)
    partial_text = Column(Text, nullable=True)
    model = Column(String, nullable=True)
    agent = Column(String, nullable=True)
    time_paused = Column(Integer, nullable=False)
    time_updated = Column(Integer, nullable=False, index=True)


class MessageTable(Base):
    __tablename__ = "message"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    parent_id = Column(String, nullable=True)
    format = Column(Text, nullable=True)  # JSON (format schema)
    # Turn number within the session — monotonic, assigned on persist.
    # Used by the rollback API to identify truncation points and by the
    # snapshot integration to tag filesystem states.
    turn_number = Column(Integer, nullable=True, index=True)
    # Shadow-git commit captured right after this turn completed (if any).
    # Rollback replays the stored patch to restore the filesystem.
    snapshot_ref = Column(String, nullable=True)
    # Assistant-specific
    model_id = Column(String, nullable=True)
    provider_id = Column(String, nullable=True)
    agent = Column(String, nullable=True)
    variant = Column(String, nullable=True)
    system = Column(Text, nullable=True)  # JSON array of system prompts
    error = Column(Text, nullable=True)  # JSON error object
    # Tokens / Cost
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    tokens_reasoning = Column(Integer, nullable=True)
    tokens_cache_read = Column(Integer, nullable=True)
    tokens_cache_write = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    # Time
    time_created = Column(Integer, nullable=False)
    time_completed = Column(Integer, nullable=True)

    __table_args__ = (
        # Canonical ordering for "give me all messages of this session in
        # turn order" — covers rebuild_history_from_db and session views.
        Index("ix_message_session_created", "session_id", "time_created"),
        Index("ix_message_session_turn", "session_id", "turn_number"),
    )


class PartTable(Base):
    __tablename__ = "part"

    id = Column(String, primary_key=True)
    message_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False)  # "text" | "tool" | "reasoning" | "file" | "step"
    # Content fields (type-dependent)
    content = Column(Text, nullable=True)  # text content / tool output / reasoning text
    # Tool-specific
    tool = Column(String, nullable=True)
    tool_call_id = Column(String, nullable=True)
    _state = Column("state", Text, nullable=True)  # JSON (tool state)
    # Time
    time_created = Column(Integer, nullable=False)
    time_completed = Column(Integer, nullable=True)

    __table_args__ = (
        # Dedupe tool calls at the DB level: each (session, tool_call_id)
        # must map to at most one part. Nulls are allowed (for non-tool
        # parts) and SQLite treats multiple NULLs as distinct, so this
        # constraint only bites actual duplicate tool-call inserts.
        UniqueConstraint("session_id", "tool_call_id", name="uq_part_session_tool_call"),
        # Fast lookup for "all parts belonging to a message in order".
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


class PermissionTable(Base):
    __tablename__ = "permission"

    project_id = Column(String, primary_key=True)
    _data = Column("data", Text, nullable=False, default="[]")  # JSON array of rules

    @property
    def data(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._data) if self._data else []
        except (json.JSONDecodeError, TypeError):
            return []

    @data.setter
    def data(self, value: list[dict[str, Any]]) -> None:
        self._data = json.dumps(value)


class CompactionEventTable(Base):
    """Tracks context compaction events and metrics."""
    __tablename__ = "compaction_event"

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    iteration = Column(Integer, nullable=False)
    # Compaction metrics
    old_message_count = Column(Integer, nullable=False)
    old_message_tokens = Column(Integer, nullable=False)
    summary_length = Column(Integer, nullable=False)
    removed_turn_count = Column(Integer, nullable=False)
    # The old messages that were removed (JSON array)
    _old_messages = Column("old_messages", Text, nullable=False)
    # The generated summary
    summary = Column(Text, nullable=False)
    # Time
    time_created = Column(Integer, nullable=False)

    @property
    def old_messages(self) -> list[dict[str, Any]]:
        """Deserialize old messages from JSON."""
        try:
            return json.loads(self._old_messages) if self._old_messages else []
        except (json.JSONDecodeError, TypeError):
            return []

    @old_messages.setter
    def old_messages(self, value: list[dict[str, Any]]) -> None:
        """Serialize old messages to JSON."""
        self._old_messages = json.dumps(value)
