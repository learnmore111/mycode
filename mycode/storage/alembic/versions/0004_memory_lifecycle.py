"""versioned long-term memory lifecycle

Revision ID: 0004_memory_lifecycle
Revises: 0003_add_raw_usage
Create Date: 2026-07-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_memory_lifecycle"
down_revision = "0003_add_raw_usage"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_record",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("root_id", sa.String(), nullable=False),
        sa.Column("memory_type", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("trigger_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_session_id", sa.String(), nullable=True),
        sa.Column("source_message_ids", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("evidence_refs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("observed_at", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Integer(), nullable=True),
        sa.Column("valid_to", sa.Integer(), nullable=True),
        sa.Column("last_verified_at", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("supersedes_id", sa.String(), nullable=True),
        sa.Column("sensitivity", sa.String(), nullable=False, server_default="normal"),
        sa.Column("extractor_version", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("time_created", sa.Integer(), nullable=False),
        sa.Column("time_updated", sa.Integer(), nullable=False),
    )
    op.create_index("ix_memory_record_root_id", "memory_record", ["root_id"])
    op.create_index("ix_memory_record_source_session_id", "memory_record", ["source_session_id"])
    op.create_index("ix_memory_record_expires_at", "memory_record", ["expires_at"])
    op.create_index("ix_memory_record_status", "memory_record", ["status"])
    op.create_index("ix_memory_record_supersedes_id", "memory_record", ["supersedes_id"])
    op.create_index("ix_memory_scope_status", "memory_record", ["scope_type", "scope_id", "status"])
    op.create_index("ix_memory_scope_hash", "memory_record", ["scope_type", "scope_id", "content_hash"])
    op.create_index("ix_memory_subject", "memory_record", ["scope_type", "scope_id", "subject"])

    op.create_table(
        "memory_audit",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("memory_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("details", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("time_created", sa.Integer(), nullable=False),
    )
    op.create_index("ix_memory_audit_memory_id", "memory_audit", ["memory_id"])
    op.create_index("ix_memory_audit_action", "memory_audit", ["action"])
    op.create_index("ix_memory_audit_time_created", "memory_audit", ["time_created"])

    op.create_table(
        "memory_extraction_state",
        sa.Column("session_id", sa.String(), primary_key=True),
        sa.Column("processed_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("time_started", sa.Integer(), nullable=False),
        sa.Column("time_completed", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("memory_extraction_state")
    op.drop_table("memory_audit")
    op.drop_table("memory_record")
