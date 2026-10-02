"""
Queued step: create a Gnani batch job, then start it.

Called by the worker loop when a job has status='queued'.
ONE action per call — the worker loop will re-claim and re-enter for the
next sub-step (create → start → advance).

This module must NOT call commit, begin, or rollback — the caller
(transactional_session in the loop) owns the transaction boundary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.constants import ERR_PROVIDER_AUTH, ERR_PROVIDER_ERROR
from app.services.gnani import GnaniAPIError, create_job, get_job, start_job
from app.services.storage import create_signed_download_url
from app.worker import fail_job, finish_step

logger = logging.getLogger(__name__)

# Gnani statuses that mean the job is still alive after a 409 on start.
_RUNNING_AFTER_CONFLICT = frozenset({
    "CREATED", "STARTING", "QUEUED", "IN_PROGRESS", "COMPLETED",
})

# Gnani statuses that mean the job died — we must fail and clear gnani fields.
_FAILED_AFTER_CONFLICT = frozenset({
    "FAILED", "START_FAILED", "CANCELLED", "PARTIAL_FAILURE",
})

_FAIL_MSG = (
    "The speech service could not read the uploaded file — please retry"
)


async def step_queued(job: dict[str, Any], deps: Any) -> None:
    """One worker tick for a job in 'queued' status.

    deps must expose:
      .session      — AsyncSession (caller manages commit/rollback)
      .gnani_client — httpx.AsyncClient for Gnani calls
    """
    session = deps.session
    job_id = job["id"]

    # ------------------------------------------------------------------
    # Branch 1: no gnani_job_id yet → get signed URL, create Gnani job
    # ------------------------------------------------------------------
    if not job.get("gnani_job_id"):
        try:
            download_url = await create_signed_download_url(
                job["storage_path"],
                expires_in=10800,
            )
        except Exception as exc:
            # Storage error getting the signed URL — retryable
            logger.info(
                "Storage signed-URL error for job %s: %s",
                job_id, type(exc).__name__,
            )
            await finish_step(
                session, job_id,
                fields={},
                next_run_in_s=30,
            )
            return

        try:
            result = await create_job(
                download_url,
                job["language_code"],
                client=deps.gnani_client,
            )
        except GnaniAPIError as exc:
            await _handle_gnani_error(exc, job_id, session)
            return

        created_id = result["job_id"]
        await finish_step(
            session, job_id,
            fields={"gnani_job_id": created_id},
            next_run_in_s=0,
        )
        return

    # ------------------------------------------------------------------
    # Branch 2: gnani_job_id present, not started → start it
    # ------------------------------------------------------------------
    if not job.get("gnani_started"):
        gnani_job_id: str = job["gnani_job_id"]

        try:
            start_resp = await start_job(
                gnani_job_id,
                client=deps.gnani_client,
            )
        except GnaniAPIError as exc:
            await _handle_gnani_error(exc, job_id, session)
            return

        # start_job returns {"status": "CONFLICT", ...} on 409 (no raise)
        if start_resp.get("status") == "CONFLICT":
            await _handle_start_conflict(gnani_job_id, job_id, deps)
            return

        # Success (2xx) — move to transcribing
        await finish_step(
            session, job_id,
            fields={
                "gnani_started": True,
                "status": "transcribing",
                "processing_started_at": datetime.now(timezone.utc),
            },
            next_run_in_s=10,
        )
        return

    # ------------------------------------------------------------------
    # Branch 3: gnani_job_id present AND gnani_started already true
    #           (stale queued row, e.g. after a manual retry that kept the id)
    # ------------------------------------------------------------------
    await finish_step(
        session, job_id,
        fields={"status": "transcribing"},
        next_run_in_s=0,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _handle_start_conflict(
    gnani_job_id: str,
    job_id: Any,
    deps: Any,
) -> None:
    """On 409 from start_job, call get_job to decide if running or failed."""
    session = deps.session

    try:
        status_resp = await get_job(gnani_job_id, client=deps.gnani_client)
    except GnaniAPIError as exc:
        await _handle_gnani_error(exc, job_id, session)
        return

    gnani_status: str = status_resp.get("status", "UNKNOWN")

    if gnani_status in _RUNNING_AFTER_CONFLICT:
        # Treat as started successfully
        await finish_step(
            session, job_id,
            fields={
                "gnani_started": True,
                "status": "transcribing",
                "processing_started_at": datetime.now(timezone.utc),
            },
            next_run_in_s=10,
        )
    elif gnani_status in _FAILED_AFTER_CONFLICT:
        await fail_job(
            session, job_id,
            ERR_PROVIDER_ERROR,
            _FAIL_MSG,
            retryable=True,
            clear_gnani=True,
        )
    else:
        # Unknown status from get_job after conflict — treat as running
        logger.warning(
            "Unknown Gnani status %r after 409 for job %s", gnani_status, job_id,
        )
        await finish_step(
            session, job_id,
            fields={
                "gnani_started": True,
                "status": "transcribing",
                "processing_started_at": datetime.now(timezone.utc),
            },
            next_run_in_s=10,
        )


async def _handle_gnani_error(
    exc: GnaniAPIError,
    job_id: Any,
    session: Any,
) -> None:
    """Map a GnaniAPIError to either a reschedule or a terminal failure."""
    err = exc.error
    if err.http_status in (401, 403):
        await fail_job(
            session, job_id,
            ERR_PROVIDER_AUTH,
            "Speech service authentication failed",
            retryable=False,
        )
    elif err.retryable:
        logger.info(
            "Transient Gnani error for job %s: %s", job_id, err.code,
        )
        await finish_step(
            session, job_id,
            fields={},
            next_run_in_s=30,
        )
    else:
        await fail_job(
            session, job_id,
            ERR_PROVIDER_ERROR,
            "Speech service error",
            retryable=False,
        )
