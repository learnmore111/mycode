"""add raw_usage column to message table

Revision ID: 0003_add_raw_usage
Revises: 0002_orchestration_run_history
Create Date: 2026-04-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_add_raw_usage"
down_revision = "0002_orchestration_run_history"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column("message", sa.Column("raw_usage", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "raw_usage")
