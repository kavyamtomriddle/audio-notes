"""
Transcribing step: poll Gnani, route by status, handle timeouts & errors.

Called by the worker loop when a job has status='transcribing'.
Every code path persists gnani_status via finish_step fields so the
frontend can show the correct stage text.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config import TRANSCRIBE_TIMEOUT_S
from app.constants import ERR_PROVIDER_AUTH, ERR_PROVIDER_ERROR, ERR_PROVIDER_TIMEOUT
from app.services.gnani import GnaniAPIError, get_files, get_job
from app.steps.completed import handle_completed
from app.worker import fail_job, finish_step

logger = logging.getLogger(__name__)

# Gnani statuses we treat as non-terminal (keep polling).
_NON_TERMINAL = frozenset({"CREATED", "STARTING", "QUEUED", "IN_PROGRESS"})

# Gnani statuses that mean the job failed on Gnani's side.
_FAILED_TERMINAL = frozenset({"START_FAILED", "FAILED", "PARTIAL_FAILURE", "CANCELLED"})

_FAIL_MSG = (
    "The speech service could not read the uploaded file — please retry"
)


async def step_transcribing(job: dict[str, Any], deps: Any) -> None:
    """One worker tick for a job in 'transcribing' status.

    deps must expose:
      .session   — AsyncSession (caller manages commit/rollback)
      .gnani_client — httpx.AsyncClient for Gnani calls
    """
    session = deps.session
    job_id = job["id"]
    gnani_job_id = job["gnani_job_id"]

    # --- Call Gnani get_job ---
    try:
        gnani_resp = await get_job(gnani_job_id, client=deps.gnani_client)
    except GnaniAPIError as exc:
        await _handle_provider_error(exc, job_id, session)
        return

    gnani_status: str = gnani_resp.get("status", "UNKNOWN")

    # --- Timeout check (Python UTC vs Python UTC) ---
    if _is_timed_out(job):
        logger.warning("Job %s timed out (gnani_status=%s)", job_id, gnani_status)
        await fail_job(
            session, job_id,
            ERR_PROVIDER_TIMEOUT,
            "Transcription timed out — please retry",
            retryable=True,
        )
        return

    # --- Route by Gnani status ---
    if gnani_status in _NON_TERMINAL:
        # Still working — reschedule 10 s, persist gnani_status
        await finish_step(
            session, job_id,
            fields={"gnani_status": gnani_status},
            next_run_in_s=10,
        )
        return

    if gnani_status == "COMPLETED":
        # Persist gnani_status before handing off
        await finish_step(
            session, job_id,
            fields={"gnani_status": gnani_status},
            next_run_in_s=0,
        )
        await handle_completed(job, deps)
        return

    if gnani_status in _FAILED_TERMINAL:
        await _handle_failed_terminal(
            gnani_status, gnani_resp, job_id, gnani_job_id, deps,
        )
        return

    # Unknown status — treat as non-terminal, log, reschedule
    logger.warning("Unknown Gnani status %r for job %s", gnani_status, job_id)
    await finish_step(
        session, job_id,
        fields={"gnani_status": gnani_status},
        next_run_in_s=10,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_timed_out(job: dict[str, Any]) -> bool:
    """Check if processing_started_at is older than TRANSCRIBE_TIMEOUT_S."""
    started = job.get("processing_started_at")
    if started is None:
        return False
    # Ensure timezone-aware comparison (both Python UTC)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return elapsed > TRANSCRIBE_TIMEOUT_S


async def _handle_provider_error(
    exc: GnaniAPIError,
    job_id: Any,
    session: Any,
) -> None:
    """Map a GnaniAPIError to either a reschedule or a terminal failure."""
    err = exc.error
    if err.code == "PROVIDER_AUTH" or not err.retryable:
        # Non-retryable (bad key / out of credits) → fail permanently
        await fail_job(
            session, job_id,
            ERR_PROVIDER_AUTH,
            err.message,
            retryable=False,
        )
    else:
        # Transient / retryable → reschedule 30 s
        logger.info("Transient Gnani error for job %s: %s", job_id, err.message)
        await finish_step(
            session, job_id,
            fields={"gnani_status": f"ERROR:{err.code}"},
            next_run_in_s=30,
        )


async def _handle_failed_terminal(
    gnani_status: str,
    gnani_resp: dict,
    job_id: Any,
    gnani_job_id: str,
    deps: Any,
) -> None:
    """Handle START_FAILED / FAILED / PARTIAL_FAILURE / CANCELLED.

    Best-effort get_files for an error_message; any exception is swallowed
    and logged.  Then fail the job (retryable, clear_gnani).
    """
    session = deps.session
    error_detail: str | None = None

    # Best-effort: try to get an error_message from /files
    try:
        files_resp = await get_files(gnani_job_id, client=deps.gnani_client)
        data = files_resp.get("data", [])
        if data and isinstance(data, list):
            error_detail = data[0].get("error_message")
    except Exception:
        # Swallow — we still fail the job even if /files fails
        logger.debug(
            "Best-effort get_files failed for job %s (gnani %s), ignoring",
            job_id, gnani_job_id, exc_info=True,
        )

    if error_detail:
        logger.info(
            "Gnani file error for job %s: %s (gnani_status=%s)",
            job_id, error_detail, gnani_status,
        )

    # Persist gnani_status before failing so the DB reflects the terminal state
    await fail_job(
        session, job_id,
        ERR_PROVIDER_ERROR,
        _FAIL_MSG,
        retryable=True,
        clear_gnani=True,
    )
