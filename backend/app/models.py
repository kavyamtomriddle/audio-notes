"""
SQLAlchemy ORM model for the `uploads` table (Context.md §5).

Uses TEXT + CHECK constraints (no PG enums) so migrations stay trivial.
Python-side defaults (default=) are provided alongside server_default so that
row creation works with any backend (including SQLite in tests). The
server_default values are authoritative for Alembic migrations on Postgres.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_code: Mapped[str] = mapped_column(
        Text, nullable=False, default="en-IN", server_default=text("'en-IN'"),
    )
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    duration_hint_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(
        Text, nullable=False,
        default="awaiting_upload",
        server_default=text("'awaiting_upload'"),
    )
    summary_status: Mapped[str] = mapped_column(
        Text, nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )

    gnani_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    gnani_started: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"),
    )
    gnani_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    files_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
    )

    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    summary_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=_utcnow, server_default=text("now()"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=_utcnow, server_default=text("now()"),
    )
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=_utcnow, server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_upload','queued','transcribing',"
            "'summarizing','completed','failed')",
            name="ck_uploads_status",
        ),
        CheckConstraint(
            "summary_status IN ('pending','done','failed')",
            name="ck_uploads_summary_status",
        ),
        Index("ix_uploads_session_created", "session_id", created_at.desc()),
        Index("ix_uploads_status_next_run", "status", "next_run_at"),
    )
