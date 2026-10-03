"""
Worker DB helpers — claim, finish, fail, release.

Phase 2b-i: only the DB functions that the worker loop will call.
No worker loop, no Gnani/LLM calls — those come in Phase 2b-ii.

All mutations use raw SQL (sqlalchemy.text) so Postgres-specific features
like FOR UPDATE SKIP LOCKED work correctly and updated_at is always set
explicitly (ORM onupdate is bypassed by raw SQL).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MAX_CLAIMS
from app.constants import ERR_WORKER_STUCK, SMOKE_SESSION_PREFIX

# ---------------------------------------------------------------------------
# claim_job — atomic SELECT … FOR UPDATE SKIP LOCKED + lease acquisition
# ---------------------------------------------------------------------------

_CLAIM_SELECT = text("""
    SELECT id, attempts
    FROM uploads
    WHERE status IN ('queued', 'transcribing', 'summarizing')
      AND next_run_at <= now()
      AND (lease_expires_at IS NULL OR lease_expires_at < now())
      AND session_id NOT LIKE :smoke_prefix
    ORDER BY next_run_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
""")

_CLAIM_UPDATE = text("""
    UPDATE uploads
    SET lease_expires_at = now() + interval '120 seconds',
        attempts = :new_attempts,
        updated_at = now()
    WHERE id = :job_id
""")

_FAIL_STUCK = text("""
    UPDATE uploads
    SET status = 'failed',
        error_code = :error_code,
        error_message = :error_message,
        retryable = true,
        lease_expires_at = NULL,
        updated_at = now()
    WHERE id = :job_id
""")


async def claim_job(session: AsyncSession) -> dict[str, Any] | None:
    """
    Atomically claim the next eligible job.

    Returns a dict with ALL columns of the uploads row (reflecting the
    post-update state: attempts incremented, lease_expires_at set), or
    None if nothing is claimable. The caller must commit/rollback.

    If attempts after increment exceeds MAX_CLAIMS, the job is marked
    failed (WORKER_STUCK, retryable) and None is returned.
    """
    result = await session.execute(
        _CLAIM_SELECT,
        {"smoke_prefix": f"{SMOKE_SESSION_PREFIX}%"},
    )
    row = result.mappings().first()
    if row is None:
        return None

    job_id: uuid.UUID = row["id"]
    new_attempts: int = row["attempts"] + 1

    # Guard: too many consecutive claims without progress → fail
    if new_attempts > MAX_CLAIMS:
        await session.execute(
            _FAIL_STUCK,
            {
                "job_id": job_id,
                "error_code": ERR_WORKER_STUCK,
                "error_message": (
                    "This job was picked up too many times without making progress. "
                    "You can retry it — if the problem persists, please re-upload."
                ),
            },
        )
        return None

    # Acquire the lease
    await session.execute(
        _CLAIM_UPDATE,
        {"job_id": job_id, "new_attempts": new_attempts},
    )

    # Re-read the full row so step functions can access all columns
    # (storage_path, language_code, gnani_job_id, etc.) without KeyError.
    # The SELECT reflects the post-update state: attempts incremented, lease set.
    full_row = await session.execute(
        text("SELECT * FROM uploads WHERE id = :job_id"),
        {"job_id": job_id},
    )
    row_mapping = full_row.mappings().first()
    return dict(row_mapping)


# ---------------------------------------------------------------------------
# finish_step — persist step outcome, clear lease, schedule next run
# ---------------------------------------------------------------------------

_FINISH_STEP = text("""
    UPDATE uploads
    SET {fields_sql},
        lease_expires_at = NULL,
        attempts = 0,
        next_run_at = now() + :interval_s * interval '1 second',
        updated_at = now()
    WHERE id = :job_id
""")


async def finish_step(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    fields: dict[str, Any],
    next_run_in_s: int = 0,
) -> None:
    """
    Write step results, clear the lease, reset attempts, schedule next run.

    `fields` is a dict of column_name → value that get SET in the UPDATE.
    The caller must commit.
    """
    if not fields:
        # At minimum clear the lease even if no fields to update
        field_clauses = ""
    else:
        # Build "col = :col" pairs for each field
        field_clauses = ", ".join(f"{col} = :{col}" for col in fields)

    # Build the full SQL with the field assignments injected
    if field_clauses:
        sql = f"""
            UPDATE uploads
            SET {field_clauses},
                lease_expires_at = NULL,
                attempts = 0,
                next_run_at = now() + :interval_s * interval '1 second',
                updated_at = now()
            WHERE id = :job_id
        """
    else:
        sql = """
            UPDATE uploads
            SET lease_expires_at = NULL,
                attempts = 0,
                next_run_at = now() + :interval_s * interval '1 second',
                updated_at = now()
            WHERE id = :job_id
        """

    params: dict[str, Any] = {"job_id": job_id, "interval_s": next_run_in_s}
    params.update(fields)

    await session.execute(text(sql), params)


# ---------------------------------------------------------------------------
# fail_job — mark a job as failed with error details
# ---------------------------------------------------------------------------

_FAIL_JOB = text("""
    UPDATE uploads
    SET status = 'failed',
        error_code = :error_code,
        error_message = :error_message,
        retryable = :retryable,
        lease_expires_at = NULL,
        updated_at = now()
    WHERE id = :job_id
""")

_FAIL_JOB_CLEAR_GNANI = text("""
    UPDATE uploads
    SET status = 'failed',
        error_code = :error_code,
        error_message = :error_message,
        retryable = :retryable,
        lease_expires_at = NULL,
        gnani_job_id = NULL,
        gnani_started = false,
        gnani_status = NULL,
        files_attempts = 0,
        updated_at = now()
    WHERE id = :job_id
""")


async def fail_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    error_code: str,
    message: str,
    retryable: bool,
    *,
    clear_gnani: bool = False,
) -> None:
    """
    Mark a job as failed. If clear_gnani is True (e.g. START_FAILED),
    also wipe gnani_job_id, gnani_started, gnani_status, files_attempts
    so a retry builds a fresh signed URL and a new Gnani job.
    The caller must commit.
    """
    sql = _FAIL_JOB_CLEAR_GNANI if clear_gnani else _FAIL_JOB
    await session.execute(
        sql,
        {
            "job_id": job_id,
            "error_code": error_code,
            "error_message": message,
            "retryable": retryable,
        },
    )


# ---------------------------------------------------------------------------
# release_lease — graceful shutdown: let another claim pick it up immediately
# ---------------------------------------------------------------------------

_RELEASE_LEASE = text("""
    UPDATE uploads
    SET lease_expires_at = NULL,
        updated_at = now()
    WHERE id = :job_id
""")


async def release_lease(
    session: AsyncSession,
    job_id: uuid.UUID,
) -> None:
    """
    Clear the lease so the job can be re-claimed immediately (graceful shutdown).
    The caller must commit.
    """
    await session.execute(_RELEASE_LEASE, {"job_id": job_id})
