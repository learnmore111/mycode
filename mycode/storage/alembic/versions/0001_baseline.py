"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-22

Captures the full schema as of April 2026: project, session (with
soft-delete `visible` column and summary columns), session_pause,
message (with `turn_number` + `snapshot_ref` for rollback), part (with
unique tool_call_id + directory-scope indices), permission, and
compaction_event.

Deployments that already use the legacy in-code ``_migrate()`` path
can adopt Alembic by stamping this revision without re-running DDL::

    MYCODE_ALEMBIC=1 alembic -c alembic.ini stamp 0001_baseline

Fresh installs run ``alembic upgrade head`` normally.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # project --------------------------------------------------------
    op.create_table(
        "project",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("worktree", sa.String(), nullable=False),
        sa.Column("vcs", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("icon_url", sa.String(), nullable=True),
        sa.Column("icon_color", sa.String(), nullable=True),
        sa.Column("time_created", sa.Integer(), nullable=False),
        sa.Column("time_updated", sa.Integer(), nullable=False),
        sa.Column("time_initialized", sa.Integer(), nullable=True),
        sa.Column("sandboxes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("commands", sa.Text(), nullable=True),
    )

    # session --------------------------------------------------------
    op.create_table(
        "session",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=True),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("directory", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("share_url", sa.String(), nullable=True),
        sa.Column("revert", sa.Text(), nullable=True),
        sa.Column("permission", sa.Text(), nullable=True),
        sa.Column("summary_additions", sa.Integer(), nullable=True),
        sa.Column("summary_deletions", sa.Integer(), nullable=True),
        sa.Column("summary_files", sa.Integer(), nullable=True),
        sa.Column("summary_diffs", sa.Text(), nullable=True),
        sa.Column("time_created", sa.Integer(), nullable=False),
        sa.Column("time_updated", sa.Integer(), nullable=False),
        sa.Column("time_compacting", sa.Integer(), nullable=True),
        sa.Column("time_archived", sa.Integer(), nullable=True),
        sa.Column("visible", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_session_project_id", "session", ["project_id"])
    op.create_index("ix_session_time_updated", "session", ["time_updated"])

    # session_pause --------------------------------------------------
    op.create_table(
        "session_pause",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("last_user_text", sa.Text(), nullable=False),
        sa.Column("partial_text", sa.Text(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("agent", sa.String(), nullable=True),
        sa.Column("time_paused", sa.Integer(), nullable=False),
        sa.Column("time_updated", sa.Integer(), nullable=False),
    )
    op.create_index("ix_session_pause_time_updated", "session_pause", ["time_updated"])

    # message --------------------------------------------------------
    op.create_table(
        "message",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("format", sa.Text(), nullable=True),
        sa.Column("turn_number", sa.Integer(), nullable=True),
        sa.Column("snapshot_ref", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("agent", sa.String(), nullable=True),
        sa.Column("variant", sa.String(), nullable=True),
        sa.Column("system", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("tokens_reasoning", sa.Integer(), nullable=True),
        sa.Column("tokens_cache_read", sa.Integer(), nullable=True),
        sa.Column("tokens_cache_write", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("time_created", sa.Integer(), nullable=False),
        sa.Column("time_completed", sa.Integer(), nullable=True),
    )
    op.create_index("ix_message_session_id", "message", ["session_id"])
    op.create_index("ix_message_turn_number", "message", ["turn_number"])
    op.create_index("ix_message_session_created", "message", ["session_id", "time_created"])
    op.create_index("ix_message_session_turn", "message", ["session_id", "turn_number"])

    # part -----------------------------------------------------------
    op.create_table(
        "part",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool", sa.String(), nullable=True),
        sa.Column("tool_call_id", sa.String(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("time_created", sa.Integer(), nullable=False),
        sa.Column("time_completed", sa.Integer(), nullable=True),
        sa.UniqueConstraint("session_id", "tool_call_id", name="uq_part_session_tool_call"),
    )
    op.create_index("ix_part_message_id", "part", ["message_id"])
    op.create_index("ix_part_session_id", "part", ["session_id"])
    op.create_index("ix_part_message_created", "part", ["message_id", "time_created"])

    # permission -----------------------------------------------------
    op.create_table(
        "permission",
        sa.Column("project_id", sa.String(), primary_key=True),
        sa.Column("data", sa.Text(), nullable=False, server_default="[]"),
    )

    # compaction_event -----------------------------------------------
    op.create_table(
        "compaction_event",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("old_message_count", sa.Integer(), nullable=False),
        sa.Column("old_message_tokens", sa.Integer(), nullable=False),
        sa.Column("summary_length", sa.Integer(), nullable=False),
        sa.Column("removed_turn_count", sa.Integer(), nullable=False),
        sa.Column("old_messages", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("time_created", sa.Integer(), nullable=False),
    )
    op.create_index("ix_compaction_event_session_id", "compaction_event", ["session_id"])


def downgrade() -> None:
    # Tables dropped in reverse order of creation to respect the (implicit
    # SQLite) FK chain. Indices are cascade-dropped with their owning tables.
    op.drop_table("compaction_event")
    op.drop_table("permission")
    op.drop_table("part")
    op.drop_table("message")
    op.drop_table("session_pause")
    op.drop_table("session")
    op.drop_table("project")
