"""
Pydantic models for API request/response validation.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ---------- POST /api/jobs/initiate ----------

class InitiateRequest(BaseModel):
    filename: str
    size_bytes: int = Field(gt=0)
    content_type: str | None = None
    duration_hint_s: float | None = Field(default=None, ge=0)
    language_code: str = "en-IN"


class InitiateResponse(BaseModel):
    id: uuid.UUID
    upload_url: str
    expires_in: int  # seconds


# ---------- POST /api/jobs/{id}/complete ----------

class CompleteResponse(BaseModel):
    id: uuid.UUID
    status: str


# ---------- GET /api/jobs/{id} ----------

class JobDetail(BaseModel):
    id: uuid.UUID
    session_id: str
    filename: str
    size_bytes: int
    content_type: str | None
    language_code: str
    duration_hint_s: float | None
    status: str
    gnani_status: str | None
    summary_status: str
    transcript: str | None
    summary: str | None
    error_code: str | None
    error_message: str | None
    retryable: bool | None
    summary_error: str | None
    created_at: datetime
    queued_at: datetime | None
    processing_started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- GET /api/jobs (list — no transcript/summary bodies) ----------

class JobListItem(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    language_code: str
    duration_hint_s: float | None
    status: str
    gnani_status: str | None
    summary_status: str
    summary_snippet: str | None  # first 140 chars of summary, or None
    error_code: str | None
    error_message: str | None
    retryable: bool | None
    created_at: datetime
    completed_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------- GET /api/config ----------

class LanguageOption(BaseModel):
    code: str
    label: str


class ConfigResponse(BaseModel):
    languages: list[LanguageOption]
    extensions: list[str]
    max_upload_bytes: int
    max_duration_hint_s: int
    est_ratio: float


# ---------- Error response ----------

class ErrorResponse(BaseModel):
    error_code: str
    message: str
    retryable: bool = False
