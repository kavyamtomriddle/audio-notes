import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Upload
from app.sweeper import (
    ABANDONED_AFTER,
    PIPELINE_TIMEOUT,
    SUMMARY_TIMEOUT,
    SweepResult,
    sweep,
    _aware,
)
from tests.conftest import _TestSessionLocal

@pytest_asyncio.fixture
async def session():
    async with _TestSessionLocal() as session:
        yield session

@pytest.fixture
def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _create_job(
    session: AsyncSession,
    status: str,
    *,
    created_at: datetime | None = None,
    queued_at: datetime | None = None,
    processing_started_at: datetime | None = None,
    updated_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
    transcript: str | None = None,
) -> uuid.UUID:
    job = Upload(
        session_id="test-session",
        filename="test.mp3",
        size_bytes=1024,
        storage_path=f"test-path-{uuid.uuid4()}",
        status=status,
    )
    if created_at:
        job.created_at = created_at
    if queued_at:
        job.queued_at = queued_at
    if processing_started_at:
        job.processing_started_at = processing_started_at
    if updated_at:
        job.updated_at = updated_at
    if lease_expires_at:
        job.lease_expires_at = lease_expires_at
    if transcript:
        job.transcript = transcript

    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job.id


@pytest.mark.asyncio
async def test_abandoned_upload_failed(session: AsyncSession, _now: datetime):
    job_id = await _create_job(
        session,
        "awaiting_upload",
        created_at=_now - ABANDONED_AFTER - timedelta(seconds=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 1
    assert results[0].action == "abandoned"
    assert results[0].job_id == job_id

    row = await session.get(Upload, job_id)
    assert row.status == "failed"
    assert row.error_code == "UPLOAD_ABANDONED"
    assert row.retryable is False
    assert _aware(row.updated_at) == _now
    assert row.lease_expires_at is None


@pytest.mark.asyncio
async def test_upload_29_min_old_untouched(session: AsyncSession, _now: datetime):
    job_id = await _create_job(
        session,
        "awaiting_upload",
        created_at=_now - ABANDONED_AFTER + timedelta(minutes=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 0

    row = await session.get(Upload, job_id)
    assert row.status == "awaiting_upload"


@pytest.mark.asyncio
async def test_queued_over_2h_failed_retryable(session: AsyncSession, _now: datetime):
    job_id = await _create_job(
        session,
        "queued",
        created_at=_now - PIPELINE_TIMEOUT - timedelta(seconds=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 1
    assert results[0].action == "timed_out"

    row = await session.get(Upload, job_id)
    assert row.status == "failed"
    assert row.error_code == "PROVIDER_TIMEOUT"
    assert row.retryable is True
    assert _aware(row.updated_at) == _now
    assert row.lease_expires_at is None


@pytest.mark.asyncio
async def test_transcribing_uses_processing_started_at_not_created_at(session: AsyncSession, _now: datetime):
    # created_at is 3 hours old, but processing_started_at is only 1 hour old.
    job_id = await _create_job(
        session,
        "transcribing",
        created_at=_now - timedelta(hours=3),
        processing_started_at=_now - timedelta(hours=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 0

    row = await session.get(Upload, job_id)
    assert row.status == "transcribing"


@pytest.mark.asyncio
async def test_retried_job_with_old_created_at_but_fresh_queued_at_untouched(session: AsyncSession, _now: datetime):
    job_id = await _create_job(
        session,
        "queued",
        created_at=_now - timedelta(hours=3),
        queued_at=_now - timedelta(hours=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 0

    row = await session.get(Upload, job_id)
    assert row.status == "queued"


@pytest.mark.asyncio
async def test_recent_queued_untouched(session: AsyncSession, _now: datetime):
    job_id = await _create_job(
        session,
        "queued",
        created_at=_now - timedelta(hours=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 0

    row = await session.get(Upload, job_id)
    assert row.status == "queued"


@pytest.mark.asyncio
async def test_summarizing_over_30min_completes_with_failed_summary_transcript_kept(session: AsyncSession, _now: datetime):
    job_id = await _create_job(
        session,
        "summarizing",
        updated_at=_now - SUMMARY_TIMEOUT - timedelta(seconds=1),
        transcript="some text",
    )
    results = await sweep(session, now=_now)
    assert len(results) == 1
    assert results[0].action == "summary_timed_out"

    row = await session.get(Upload, job_id)
    assert row.status == "completed"
    assert row.summary_status == "failed"
    assert row.summary_error == "Summary timed out"
    assert row.transcript == "some text"
    assert _aware(row.completed_at) == _now
    assert _aware(row.updated_at) == _now
    assert row.lease_expires_at is None


@pytest.mark.asyncio
async def test_live_lease_skipped(session: AsyncSession, _now: datetime):
    # A job that is old enough to sweep but has a live lease should be skipped.
    job_id = await _create_job(
        session,
        "awaiting_upload",
        created_at=_now - timedelta(hours=1),
        lease_expires_at=_now + timedelta(minutes=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 0

    row = await session.get(Upload, job_id)
    assert row.status == "awaiting_upload"


@pytest.mark.asyncio
async def test_expired_lease_swept(session: AsyncSession, _now: datetime):
    # A job old enough to sweep, with an expired lease, should be swept.
    job_id = await _create_job(
        session,
        "awaiting_upload",
        created_at=_now - timedelta(hours=1),
        lease_expires_at=_now - timedelta(minutes=1),
    )
    results = await sweep(session, now=_now)
    assert len(results) == 1
    assert results[0].action == "abandoned"

    row = await session.get(Upload, job_id)
    assert row.status == "failed"
    assert row.lease_expires_at is None


@pytest.mark.asyncio
async def test_completed_and_failed_rows_untouched(session: AsyncSession, _now: datetime):
    # Create very old completed and failed rows.
    completed_id = await _create_job(session, "completed", created_at=_now - timedelta(days=10))
    failed_id = await _create_job(session, "failed", created_at=_now - timedelta(days=10))
    
    results = await sweep(session, now=_now)
    assert len(results) == 0
    
    completed_row = await session.get(Upload, completed_id)
    failed_row = await session.get(Upload, failed_id)
    assert completed_row.status == "completed"
    assert failed_row.status == "failed"


@pytest.mark.asyncio
async def test_returns_storage_paths_and_actions(session: AsyncSession, _now: datetime):
    job1_id = await _create_job(
        session, "awaiting_upload", created_at=_now - timedelta(hours=1)
    )
    job2_id = await _create_job(
        session, "queued", created_at=_now - timedelta(hours=3)
    )
    
    row1 = await session.get(Upload, job1_id)
    row2 = await session.get(Upload, job2_id)
    
    results = await sweep(session, now=_now)
    assert len(results) == 2
    
    res1 = next(r for r in results if r.job_id == job1_id)
    res2 = next(r for r in results if r.job_id == job2_id)
    
    assert res1.action == "abandoned"
    assert res1.storage_path == row1.storage_path
    assert res2.action == "timed_out"
    assert res2.storage_path == row2.storage_path


@pytest.mark.asyncio
async def test_naive_datetimes_from_sqlite_handled(session: AsyncSession, _now: datetime):
    # SQLite returns naive datetimes. We force naive datetimes directly via SQL.
    from sqlalchemy import text
    
    # We'll construct the job via ORM, then update its created_at to a naive string.
    job_id = await _create_job(session, "awaiting_upload")
    
    # Update to a naive string format commonly used by SQLite
    old_time = _now - timedelta(hours=1)
    old_str = old_time.strftime("%Y-%m-%d %H:%M:%S.%f")
    await session.execute(text("UPDATE uploads SET created_at = :t WHERE id = :id"), {"t": old_str, "id": job_id.hex})
    await session.commit()
    
    # The session will now load the row with a naive datetime in SQLite.
    session.expire_all()
    row = await session.get(Upload, job_id)
    
    # Depending on dialect, it may or may not be naive. But our _aware() function
    # must handle it without raising TypeError.
    results = await sweep(session, now=_now)
    assert len(results) == 1
    assert results[0].action == "abandoned"
