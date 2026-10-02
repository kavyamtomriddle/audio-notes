"""
API routes for job management (Context.md §7).

All routes except /api/config require the X-Session-Id header.
Job access is scoped to session_id (404 otherwise).
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    EST_RATIO,
    JOBS_GLOBAL_PER_DAY,
    JOBS_PER_SESSION_PER_DAY,
    MAX_DURATION_HINT_S,
    MAX_UPLOAD_BYTES,
)
from app.constants import ALLOWED_EXTENSIONS, LANGUAGE_CODES, LANGUAGES
from app.db import get_db
from app.models import Upload
from app.schemas import (
    CompleteResponse,
    ConfigResponse,
    ErrorResponse,
    InitiateRequest,
    InitiateResponse,
    JobDetail,
    JobListItem,
    LanguageOption,
)
from app.services import storage as storage_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _error(status: int, code: str, message: str, retryable: bool = False):
    """Raise an HTTPException with the standard error body."""
    raise HTTPException(
        status_code=status,
        detail=ErrorResponse(
            error_code=code, message=message, retryable=retryable
        ).model_dump(),
    )


def _safe_filename(raw: str) -> str:
    """Sanitize filename for storage path (keep extension, replace bad chars)."""
    # Keep only alphanum, dash, underscore, dot; replace spaces with underscores.
    cleaned = ""
    for ch in raw:
        if ch.isalnum() or ch in "-_.":
            cleaned += ch
        elif ch == " ":
            cleaned += "_"
    return cleaned or "audio"


def _get_extension(filename: str) -> str | None:
    """Return the lowercase extension without the dot, or None."""
    if "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1].lower()


# ---------- GET /api/config ----------

@router.get("/config", response_model=ConfigResponse)
async def get_config():
    """Static settings: no session header, no secrets, no DB access."""
    return ConfigResponse(
        languages=[LanguageOption(code=c, label=l) for c, l in LANGUAGES],
        extensions=ALLOWED_EXTENSIONS,
        max_upload_bytes=MAX_UPLOAD_BYTES,
        max_duration_hint_s=MAX_DURATION_HINT_S,
        est_ratio=EST_RATIO,
    )


# ---------- POST /api/jobs/initiate ----------

@router.post(
    "/jobs/initiate",
    response_model=InitiateResponse,
    status_code=201,
    responses={413: {"model": ErrorResponse}, 415: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
async def initiate_job(
    body: InitiateRequest,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
):
    """Validate, create row, get signed upload URL."""

    # --- Extension check ---
    ext = _get_extension(body.filename)
    if ext is None or ext not in ALLOWED_EXTENSIONS:
        _error(
            415,
            "UNSUPPORTED_FORMAT",
            f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # --- Size check ---
    if body.size_bytes > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        _error(
            413,
            "FILE_TOO_LARGE",
            f"File exceeds the {mb} MB limit. Try compressing to MP3 (~1 MB/min at 128 kbps).",
        )

    # --- Duration hint check ---
    if body.duration_hint_s is not None and body.duration_hint_s > MAX_DURATION_HINT_S:
        _error(
            413,
            "FILE_TOO_LARGE",
            f"Audio duration exceeds the {MAX_DURATION_HINT_S // 3600}-hour limit.",
        )

    # --- Language code check ---
    if body.language_code not in LANGUAGE_CODES:
        _error(
            415,
            "UNSUPPORTED_FORMAT",
            f"Unsupported language code '{body.language_code}'. "
            f"Supported: {', '.join(sorted(LANGUAGE_CODES))}",
        )

    # --- Daily caps (§12) ---
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Per-session cap
    session_count_q = select(func.count()).select_from(Upload).where(
        Upload.session_id == x_session_id,
        Upload.created_at >= today_start,
    )
    session_count = (await db.execute(session_count_q)).scalar() or 0
    if session_count >= JOBS_PER_SESSION_PER_DAY:
        _error(
            429,
            "RATE_LIMITED_SESSION",
            f"You've reached the daily limit of {JOBS_PER_SESSION_PER_DAY} uploads per session.",
        )

    # Global cap
    global_count_q = select(func.count()).select_from(Upload).where(
        Upload.created_at >= today_start,
    )
    global_count = (await db.execute(global_count_q)).scalar() or 0
    if global_count >= JOBS_GLOBAL_PER_DAY:
        _error(
            429,
            "DAILY_CAP_REACHED",
            "The platform's daily upload limit has been reached. Please try again tomorrow.",
        )

    # --- Create row ---
    job_id = uuid.uuid4()
    safe_name = _safe_filename(body.filename)
    storage_path = f"{x_session_id}/{job_id}/{safe_name}"

    upload = Upload(
        id=job_id,
        session_id=x_session_id,
        filename=body.filename,
        size_bytes=body.size_bytes,
        content_type=body.content_type,
        language_code=body.language_code,
        storage_path=storage_path,
        duration_hint_s=body.duration_hint_s,
        status="awaiting_upload",
        summary_status="pending",
    )
    db.add(upload)
    await db.flush()  # get the row in DB before calling storage

    # --- Signed upload URL ---
    upload_url, expires_in = await storage_svc.create_signed_upload_url(storage_path)

    return InitiateResponse(id=job_id, upload_url=upload_url, expires_in=expires_in)


# ---------- POST /api/jobs/{id}/complete ----------

@router.post(
    "/jobs/{job_id}/complete",
    response_model=CompleteResponse,
    responses={409: {"model": ErrorResponse}},
)
async def complete_upload(
    job_id: uuid.UUID,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
):
    """Verify the object was uploaded, transition to queued."""
    upload = await _get_job_for_session(db, job_id, x_session_id)

    if upload.status != "awaiting_upload":
        _error(409, "UPLOAD_INCOMPLETE", f"Job is '{upload.status}', not 'awaiting_upload'.")

    # Verify the object exists in storage
    exists = await storage_svc.object_exists(upload.storage_path)
    if not exists:
        _error(
            409,
            "UPLOAD_INCOMPLETE",
            "The file has not been uploaded yet or the upload was incomplete.",
        )

    upload.status = "queued"
    upload.queued_at = datetime.now(timezone.utc)
    upload.updated_at = datetime.now(timezone.utc)

    return CompleteResponse(id=upload.id, status=upload.status)


# ---------- GET /api/jobs/{id} ----------

@router.get("/jobs/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: uuid.UUID,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
):
    """Full job detail (session-scoped)."""
    upload = await _get_job_for_session(db, job_id, x_session_id)
    return JobDetail.model_validate(upload)


# ---------- GET /api/jobs ----------

@router.get("/jobs", response_model=list[JobListItem])
async def list_jobs(
    x_session_id: str = Header(..., alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
):
    """This session's jobs, newest first, without transcript/summary bodies."""
    q = (
        select(Upload)
        .where(Upload.session_id == x_session_id)
        .order_by(Upload.created_at.desc())
    )
    result = await db.execute(q)
    uploads = result.scalars().all()

    items = []
    for u in uploads:
        snippet = u.summary[:140] if u.summary else None
        items.append(
            JobListItem(
                id=u.id,
                filename=u.filename,
                size_bytes=u.size_bytes,
                language_code=u.language_code,
                duration_hint_s=u.duration_hint_s,
                status=u.status,
                gnani_status=u.gnani_status,
                summary_status=u.summary_status,
                summary_snippet=snippet,
                error_code=u.error_code,
                error_message=u.error_message,
                retryable=u.retryable,
                created_at=u.created_at,
                completed_at=u.completed_at,
                updated_at=u.updated_at,
            )
        )
    return items


# ---------- Helpers ----------

async def _get_job_for_session(
    db: AsyncSession,
    job_id: uuid.UUID,
    session_id: str,
) -> Upload:
    """Fetch a job row and verify session ownership. 404 if not found or wrong session."""
    q = select(Upload).where(Upload.id == job_id)
    result = await db.execute(q)
    upload = result.scalar_one_or_none()
    if upload is None or upload.session_id != session_id:
        raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND", "message": "Job not found.", "retryable": False})
    return upload


# ---------- POST /api/jobs/{id}/retry ----------

@router.post("/jobs/{job_id}/retry", response_model=JobDetail)
async def retry_job(
    job_id: uuid.UUID,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed and retryable job."""
    upload = await _get_job_for_session(db, job_id, x_session_id)

    if upload.status != "failed" or not upload.retryable:
        _error(409, "NOT_RETRYABLE", "Job is not in a failed and retryable state.")

    now = datetime.now(timezone.utc)
    upload.status = "queued"
    upload.attempts = 0
    upload.files_attempts = 0
    upload.error_code = None
    upload.error_message = None
    upload.retryable = None
    upload.summary_status = "pending"
    upload.next_run_at = now
    upload.lease_expires_at = None
    upload.queued_at = now
    upload.processing_started_at = None
    upload.updated_at = now
    
    await db.flush()
    return JobDetail.model_validate(upload)


# ---------- POST /api/jobs/{id}/retry-summary ----------

@router.post("/jobs/{job_id}/retry-summary", response_model=JobDetail)
async def retry_summary(
    job_id: uuid.UUID,
    x_session_id: str = Header(..., alias="X-Session-Id"),
    db: AsyncSession = Depends(get_db),
):
    """Retry a failed summary step."""
    upload = await _get_job_for_session(db, job_id, x_session_id)

    if not upload.transcript or upload.summary_status != "failed":
        _error(409, "NOT_RETRYABLE_SUMMARY", "Cannot retry summary unless transcript exists and summary failed.")

    now = datetime.now(timezone.utc)
    upload.status = "summarizing"
    upload.summary_status = "pending"
    upload.summary_error = None
    upload.next_run_at = now
    upload.lease_expires_at = None
    upload.attempts = 0
    upload.updated_at = now

    await db.flush()
    return JobDetail.model_validate(upload)
