"""orchestration run history

Revision ID: 0002_orchestration_run_history
Revises: 0001_baseline
Create Date: 2026-04-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_orchestration_run_history"
down_revision = "0001_baseline"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "orchestration_run",
        sa.Column("run_id", sa.String(), primary_key=True),
        sa.Column("flow", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("directory", sa.String(), nullable=True),
        sa.Column("task_text", sa.Text(), nullable=True),
        sa.Column("vars", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("walltime_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
    )
    op.create_index("ix_orchestration_run_started_at", "orchestration_run", ["started_at"])
    op.create_index(
        "ix_orchestration_run_status_started",
        "orchestration_run",
        ["status", "started_at"],
    )


def downgrade() -> None:
    op.drop_table("orchestration_run")
