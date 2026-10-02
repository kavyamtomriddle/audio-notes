"""create uploads table

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("language_code", sa.Text(), nullable=False, server_default=sa.text("'en-IN'")),
        sa.Column("storage_path", sa.Text(), nullable=False, unique=True),
        sa.Column("duration_hint_s", sa.Float(), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False,
            server_default=sa.text("'awaiting_upload'"),
        ),
        sa.Column(
            "summary_status", sa.Text(), nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("gnani_job_id", sa.Text(), nullable=True),
        sa.Column("gnani_started", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("gnani_status", sa.Text(), nullable=True),
        sa.Column("files_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("summary_error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        # CHECK constraints (TEXT, not PG enums, so migrations stay trivial)
        sa.CheckConstraint(
            "status IN ('awaiting_upload','queued','transcribing','summarizing','completed','failed')",
            name="ck_uploads_status",
        ),
        sa.CheckConstraint(
            "summary_status IN ('pending','done','failed')",
            name="ck_uploads_summary_status",
        ),
    )
    # Indexes (Context.md §5)
    op.create_index(
        "ix_uploads_session_created",
        "uploads",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_uploads_status_next_run",
        "uploads",
        ["status", "next_run_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_uploads_status_next_run", table_name="uploads")
    op.drop_index("ix_uploads_session_created", table_name="uploads")
    op.drop_table("uploads")
