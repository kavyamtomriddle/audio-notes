"""Tests for POST /api/jobs/{id}/retry and /retry-summary."""

import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from app.models import Upload
from app.db import get_db
import pytest_asyncio
from tests.conftest import _TestSessionLocal

SESSION = "test-session-retry"
HEADERS = {"X-Session-Id": SESSION}

@pytest_asyncio.fixture
async def db_session():
    async with _TestSessionLocal() as session:
        yield session

async def _create_job(db: AsyncSession, **kwargs) -> uuid.UUID:
    job_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    attrs = dict(
        id=job_id,
        session_id=SESSION,
        filename="test.mp3",
        size_bytes=1024,
        language_code="en-IN",
        storage_path=f"{SESSION}/{job_id}/test.mp3",
        status="failed",
        retryable=True,
        error_code="PROVIDER_ERROR",
        error_message="Oops",
        gnani_job_id="g-123",
        gnani_started=True,
        gnani_status="FAILED",
        created_at=now,
        updated_at=now,
    )
    attrs.update(kwargs)
    upload = Upload(**attrs)
    db.add(upload)
    await db.commit()
    return job_id

@pytest.mark.asyncio
async def test_retry_requeues_failed_retryable_job(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, attempts=3, files_attempts=2)
    
    resp = await client.post(f"/api/jobs/{job_id}/retry", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["status"] == "queued"
    assert data["error_code"] is None
    assert data["error_message"] is None
    assert data["retryable"] is None
    assert data["summary_status"] == "pending"

@pytest.mark.asyncio
async def test_retry_keeps_gnani_job_id(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, gnani_job_id="keep-me", gnani_started=True, gnani_status="IN_PROGRESS")
    
    resp = await client.post(f"/api/jobs/{job_id}/retry", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["gnani_status"] == "IN_PROGRESS"
    
    # Check db
    await db_session.refresh(await db_session.get(Upload, job_id))
    db_job = await db_session.get(Upload, job_id)
    assert db_job.gnani_job_id == "keep-me"
    assert db_job.gnani_started is True
    assert db_job.gnani_status == "IN_PROGRESS"

@pytest.mark.asyncio
async def test_retry_resets_queued_at_and_processing_started_at(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(
        db_session, 
        queued_at=datetime(2000, 1, 1, tzinfo=timezone.utc), 
        processing_started_at=datetime(2000, 1, 1, tzinfo=timezone.utc)
    )
    
    resp = await client.post(f"/api/jobs/{job_id}/retry", headers=HEADERS)
    assert resp.status_code == 200
    
    await db_session.refresh(await db_session.get(Upload, job_id))
    db_job = await db_session.get(Upload, job_id)
    queued_at = db_job.queued_at.replace(tzinfo=timezone.utc) if db_job.queued_at.tzinfo is None else db_job.queued_at
    assert queued_at > datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert db_job.processing_started_at is None
    assert db_job.attempts == 0
    assert db_job.files_attempts == 0
    assert db_job.lease_expires_at is None

@pytest.mark.asyncio
async def test_retry_non_retryable_409(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, status="failed", retryable=False)
    
    resp = await client.post(f"/api/jobs/{job_id}/retry", headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "NOT_RETRYABLE"

@pytest.mark.asyncio
async def test_retry_not_failed_409(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, status="queued", retryable=True)
    
    resp = await client.post(f"/api/jobs/{job_id}/retry", headers=HEADERS)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_retry_foreign_session_404(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session)
    
    resp = await client.post(f"/api/jobs/{job_id}/retry", headers={"X-Session-Id": "other"})
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_retry_unknown_id_404(client: AsyncClient):
    resp = await client.post(f"/api/jobs/{uuid.uuid4()}/retry", headers=HEADERS)
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_retry_summary_success(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(
        db_session, 
        status="completed", 
        transcript="some text", 
        summary_status="failed", 
        summary_error="LLM failed",
        attempts=3
    )
    
    resp = await client.post(f"/api/jobs/{job_id}/retry-summary", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "summarizing"
    assert data["summary_status"] == "pending"
    
    await db_session.refresh(await db_session.get(Upload, job_id))
    db_job = await db_session.get(Upload, job_id)
    assert db_job.summary_error is None
    assert db_job.lease_expires_at is None
    assert db_job.attempts == 0
    assert db_job.next_run_at is not None

@pytest.mark.asyncio
async def test_retry_summary_without_transcript_409(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, status="completed", transcript=None, summary_status="failed")
    
    resp = await client.post(f"/api/jobs/{job_id}/retry-summary", headers=HEADERS)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_retry_summary_when_summary_done_409(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, status="completed", transcript="some text", summary_status="done")
    
    resp = await client.post(f"/api/jobs/{job_id}/retry-summary", headers=HEADERS)
    assert resp.status_code == 409

@pytest.mark.asyncio
async def test_retry_summary_foreign_session_404(client: AsyncClient, db_session: AsyncSession):
    job_id = await _create_job(db_session, status="completed", transcript="text", summary_status="failed")
    
    resp = await client.post(f"/api/jobs/{job_id}/retry-summary", headers={"X-Session-Id": "other"})
    assert resp.status_code == 404
